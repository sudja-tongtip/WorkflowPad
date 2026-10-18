"""
Gallery Generator – generates images repeatedly until stopped or the target count is reached.
Displays gallery thumbnails: click to select (Ctrl/Shift for several), drag to select a group,
double-click to view large, Delete to remove the selected images.
The Working Folder owns its images and its gallery data (.gallery/gallery.json inside it).
"""
import asyncio
import base64
import json
import os
import random
import shutil
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import flet as ft

# ── App metadata ──────────────────────
APP_ID      = "gallery_generator"
APP_NAME    = "Gallery Generator"
APP_VERSION = "0.1.5"   # SemVer; see RELEASE.md

# ── Config ────────────────────────────────────────────────────────────
COMFY_HOST = "127.0.0.1:8188"
# Folder with comfy_apis/ (image_create, image_edit, llm, caption, enhance), icons/: the project folder, or (PyInstaller) the folder next to the EXE.
APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
API_DIR    = APP_ROOT / "comfy_apis" / "image_create"
USER_CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "WorkflowPad"     # user data: never next to the program
SAVE_PATH  = USER_CONFIG_DIR / "saved_gallery.json"
try:
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    for _n in ("saved_gallery.json", "saved_gallery.legacy.json"):                         # one-time copy from an earlier build (the old files are kept)
        if (APP_ROOT / _n).is_file() and not (USER_CONFIG_DIR / _n).exists():
            shutil.copy2(APP_ROOT / _n, USER_CONFIG_DIR / _n)
except OSError:
    pass

# ── Palette ───────────────────────────────────────────────────────────
C = {
    "bg"        : "#F0F0F0",
    "surface"   : "#FFFFFF",
    "panel"     : "#F7F7F8",
    "border"    : "#E2E2E6",
    "border2"   : "#D0D0D6",
    "accent"    : "#2563EB",
    "accent_lt" : "#EEF3FE",
    "green"     : "#16A34A",
    "green_lt"  : "#EDFAF3",
    "red"       : "#DC2626",
    "red_lt"    : "#FEF2F2",
    "text1"     : "#111111",
    "text2"     : "#555560",
    "text3"     : "#9999A8",
    "amber"     : "#D97706",
}

def po(left=0, top=0, right=0, bottom=0):
    return ft.Padding(left=left, top=top, right=right, bottom=bottom)

def br(r): return ft.BorderRadius(r, r, r, r)

def divider(color=None):
    return ft.Divider(height=1, color=color or C["border"])

# ── ComfyUI API ──────────────────────────────────────────────────────
def comfy_upload_image(path: str) -> str:
    with open(path, "rb") as f: data = f.read()
    filename = Path(path).name
    boundary = "----B" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
        f"filename=\"{filename}\"\r\nContent-Type: image/jpeg\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"http://{COMFY_HOST}/upload/image", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["name"]

def comfy_queue(wf: dict, client_id: str) -> str:
    payload = json.dumps({"prompt": wf, "client_id": client_id}).encode()
    req = urllib.request.Request(
        f"http://{COMFY_HOST}/prompt", data=payload,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["prompt_id"]

def comfy_history(pid: str) -> dict:
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

def comfy_get_image(filename, subfolder, folder_type) -> bytes:
    p = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": folder_type})
    with urllib.request.urlopen(f"http://{COMFY_HOST}/view?{p}", timeout=30) as r:
        return r.read()

# ── Workflow auto-detection ───────────────────────────────────────────
_IMG_CLASSES    = ["LoadImage", "ETN_LoadImageBase64", "LoadImageMask",
                   "ImageBatch", "LoadImageFromUrl"]
_PROMPT_CLASSES = ["CLIPTextEncodeFlux", "CLIPTextEncodeSD3", "CLIPTextEncodeHunyuan",
                   "smZ CLIPTextEncode", "BNK_CLIPTextEncodeAdvanced", "CLIPTextEncode"]
_SEED_CLASSES   = ["RandomNoise", "KSamplerAdvanced", "KSampler",
                   "SamplerCustomAdvanced", "SamplerCustom"]
_SEED_FIELDS    = ["noise_seed", "seed", "rand_seed"]
_POS_HINTS      = {"positive", "pos", "prompt", "clip positive", "positive prompt"}

def _title(nid, wf):
    return (wf.get(nid, {}).get("_meta", {}).get("title", "") or "").lower()

def detect_mapping(wf: dict) -> dict:
    """Analyze the workflow to find image/prompt/seed nodes.
    Also returns has_image_input: whether the workflow requires an image."""
    img_node = img_field = seed_node = seed_field = None
    candidates = []
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        ct = node.get("class_type", ""); inp = node.get("inputs", {})
        if ct in _IMG_CLASSES:
            if "image" in inp: img_node, img_field = nid, "image"
            elif not img_node:
                for k, v in inp.items():
                    if not isinstance(v, list): img_node, img_field = nid, k; break
        prio = next((i for i, c in enumerate(_PROMPT_CLASSES) if c == ct), None)
        if prio is None and "clip" in ct.lower() and "encode" in ct.lower(): prio = len(_PROMPT_CLASSES)
        if prio is None:
            for f in ("text", "prompt", "positive", "negative"):
                if f in inp and isinstance(inp[f], str): prio = len(_PROMPT_CLASSES) + 10; break
        if prio is not None:
            tf = next((f for f in ("text", "prompt", "positive", "negative")
                       if f in inp and isinstance(inp[f], str)), None)
            if tf is None: tf = next((k for k, v in inp.items() if isinstance(v, str) and v), "text")
            t = _title(nid, wf)
            role = "negative" if "neg" in t else ("positive" if any(h in t for h in _POS_HINTS) else "unknown")      # 'Negative Prompt' contains 'prompt': negative first
            candidates.append((prio, nid, tf, role))
    candidates.sort(key=lambda x: (x[0], 0 if x[3] == "positive" else 1))
    prompt_nodes = []; seen = False
    for p, nid, tf, role in candidates:
        if role == "unknown" and not seen: role = "positive"
        if role == "positive": seen = True
        prompt_nodes.append({"node": nid, "field": tf, "role": role})
    for cls in _SEED_CLASSES:
        for nid, node in wf.items():
            if not isinstance(node, dict): continue
            if node.get("class_type", "") == cls:
                for sf in _SEED_FIELDS:
                    if sf in node.get("inputs", {}): seed_node, seed_field = nid, sf; break
                if seed_node: break
        if seed_node: break
    if not seed_node:
        for nid, node in wf.items():
            if not isinstance(node, dict): continue
            for sf in _SEED_FIELDS:
                if sf in node.get("inputs", {}) and isinstance(node["inputs"][sf], (int, float)):
                    seed_node, seed_field = nid, sf; break
            if seed_node: break
    return {
        "image_node": img_node, "image_field": img_field or "image",
        "prompt_nodes": prompt_nodes,
        "seed_node": seed_node, "seed_field": seed_field or "noise_seed",
        "has_image_input": img_node is not None,
    }

def build_workflow(api_filename: str, prompt_text: str,
                   uploaded_image: str | None = None,
                   width: int = 0, height: int = 0) -> dict:
    path = API_DIR / api_filename
    with open(path, encoding="utf-8") as f: wf = json.load(f)
    m = detect_mapping(wf)
    if m["image_node"] and uploaded_image:
        wf[m["image_node"]]["inputs"][m["image_field"]] = uploaded_image
    for ph in m["prompt_nodes"]:
        if ph["role"] in ("positive", "unknown"):
            wf[ph["node"]]["inputs"][ph["field"]] = prompt_text; break
    if m["seed_node"]:
        wf[m["seed_node"]]["inputs"][m["seed_field"]] = random.randint(0, 2**53)
    if width > 0 and height > 0:
        wf = inject_size(wf, width, height)
    return wf

# ── Size injection ───────────────────────────────────────────────────
_LATENT_CLS = [
    "EmptyLatentImage", "EmptySD3LatentImage", "EmptyHunyuanLatentVideo",
    "EmptyMochiLatentVideo", "EmptyLTXVLatentVideo", "EmptyKoalaLatentVideo",
    "EmptyImagineLatentVideo", "EmptyLatentImagePresets",
]

def inject_size(wf: dict, width: int, height: int) -> dict:
    """Inject width/height into Latent nodes only (similar to prompt_tester)."""
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        ct  = node.get("class_type", "")
        inp = node.get("inputs", {})
        if ct in _LATENT_CLS:
            if "width"  in inp: wf[nid]["inputs"]["width"]  = width
            if "height" in inp: wf[nid]["inputs"]["height"] = height
    return wf

import re as _re
_WILDCARD_RE = _re.compile(r"\{([^{}]+)\}")

def resolve_wildcards(text: str) -> str:
    """Replace {a|b|c} with a randomly selected option — supports multiple groups in one string.
    Use | to separate options only (commas are treated as part of the option text).
    Example: '{red|tall},{blue|slow}' → 'red,blue'
             '{red, bright|blue}' → 'red, bright' or 'blue'"""
    def _pick(match: "_re.Match") -> str:
        options = [o.strip() for o in match.group(1).split("|") if o.strip()]
        return random.choice(options) if options else ""
    return _WILDCARD_RE.sub(_pick, text)

async def run_one(prompt_text: str, out_dir: Path, api_filename: str,
                  image_path: str | None = None, on_progress=None,
                  index: int = 0, width: int = 0, height: int = 0,
                  stop_event: asyncio.Event | None = None) -> str | None:
    loop = asyncio.get_event_loop()
    client_id = uuid.uuid4().hex
    uploaded = None
    if image_path:
        uploaded = await loop.run_in_executor(None, comfy_upload_image, image_path)
    wf  = build_workflow(api_filename, prompt_text, uploaded, width=width, height=height)
    pid = await loop.run_in_executor(None, comfy_queue, wf, client_id)
    _ACTIVE_PID[0] = pid
    tick = 0; deadline = time.time() + 300
    try:
        while time.time() < deadline:
            if stop_event is not None and stop_event.is_set():
                await loop.run_in_executor(None, comfy_cancel, pid)
                return None
            await asyncio.sleep(2); tick += 1
            if on_progress: on_progress(f"🔄 {tick*2}s")
            hist = await loop.run_in_executor(None, comfy_history, pid)
            if pid in hist:
                for out in hist[pid].get("outputs", {}).values():
                    if "images" in out:
                        info = out["images"][0]
                        img  = await loop.run_in_executor(None, comfy_get_image,
                               info["filename"], info.get("subfolder", ""), info.get("type", "output"))
                        out_dir.mkdir(parents=True, exist_ok=True)
                        ts = time.strftime("%Y%m%d_%H%M%S")
                        dst = out_dir / f"gen_{ts}_{uuid.uuid4().hex[:6]}.jpg"
                        dst.write_bytes(img)
                        return str(dst)
                break
    finally:
        _ACTIVE_PID[0] = None
    return None

def _to_src(path: str) -> str:
    """Convert a local path to a full-size base64 data URI (cached) — used by the lightbox."""
    try:
        p = Path(path)
        mtime = p.stat().st_mtime
        cache_key = (str(p), mtime)
        cached = _SRC_CACHE.get(cache_key)
        if cached:
            return cached
        data = p.read_bytes()
        ext  = p.suffix.lower().lstrip(".")
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png",
                "webp": "webp", "bmp": "bmp"}.get(ext, "jpeg")
        b64  = base64.b64encode(data).decode()
        src  = f"data:image/{mime};base64,{b64}"
        _SRC_CACHE[cache_key] = src
        return src
    except Exception:
        return path

def _to_thumb_src(path: str, max_size: int = 200) -> str:
    """Convert a local path to a small base64 thumbnail (cached) — used by the gallery grid.
    Faster than _to_src because it reduces the resolution before encoding."""
    try:
        p = Path(path)
        mtime = p.stat().st_mtime
        cache_key = (str(p), mtime, max_size)
        cached = _THUMB_CACHE.get(cache_key)
        if cached:
            return cached
        try:
            from PIL import Image
            import io
            img = Image.open(p)
            img.thumbnail((max_size, max_size))
            buf = io.BytesIO()
            fmt = "JPEG" if img.mode != "RGBA" else "PNG"
            if fmt == "JPEG" and img.mode != "RGB":
                img = img.convert("RGB")
            img.save(buf, format=fmt, quality=80)
            b64 = base64.b64encode(buf.getvalue()).decode()
            mime = "jpeg" if fmt == "JPEG" else "png"
            src = f"data:image/{mime};base64,{b64}"
        except ImportError:
            # No PIL available → fall back to the full image
            src = _to_src(path)
        _THUMB_CACHE[cache_key] = src
        return src
    except Exception:
        return path

_SRC_CACHE: dict = {}
_THUMB_CACHE: dict = {}

def scan_api_files() -> list[str]:
    return sorted(p.name for p in API_DIR.glob("*.json") if p.name != SAVE_PATH.name)

# ── Persistence ───────────────────────────────────────────────────────
# Two kinds of state, deliberately separate:
#   GLOBAL (saved_gallery.json beside the app): last Working Folder, selected workflow, source image, and a
#       "draft" of the work setup while no folder is chosen yet. No image list lives here any more.
#   WORKING FOLDER (<folder>/.gallery/gallery.json): the prompts, generation setup and image metadata of
#       THAT folder. The filesystem decides which images exist; this file only describes them. File names
#       are stored relative to the folder, so the whole folder can be moved or copied.
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}        # what a Working Folder scan picks up
GALLERY_DIR_NAME = ".gallery"
GALLERY_FILE_NAME = "gallery.json"
LEGACY_BACKUP_PATH = SAVE_PATH.with_name("saved_gallery.legacy.json")
_DEFAULT_SIZE = "[16:9] 2304 × 1296"
# Source Image is hidden in this version. While False the saved source image is never fed to a workflow and
# a workflow that needs one cannot run. The image-input code below is kept so the feature can come back.
SOURCE_IMAGE_ENABLED = False
_LEGACY_KEYS = ("gallery", "prompts", "output_folder", "api_rotation", "rounds_count", "sel_size", "wildcard_text")


def _atomic_write_json(path: Path, data) -> None:
    """Write complete JSON to a temp file, then replace the target — an interrupted save cannot leave a
    half-written file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load() -> dict:
    if SAVE_PATH.exists():
        try:
            d = json.loads(SAVE_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict): return d
        except Exception:
            pass
    return {}


def _save(data: dict):
    _atomic_write_json(SAVE_PATH, data)


def _gallery_file(folder: Path) -> Path:
    return folder / GALLERY_DIR_NAME / GALLERY_FILE_NAME


def _scan_images(folder: Path) -> dict:
    """{lower-case file name: Path} of the image files directly inside folder. Sub-folders (including
    .gallery) and hidden files are not indexed."""
    out = {}
    try:
        for p in folder.iterdir():
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS and not p.name.startswith("."):
                out[p.name.lower()] = p
    except OSError as ex:
        print(f"scan error: {folder}: {ex}")
    return out


def _parse_selected_apis(v):
    """[{"file", "alias"}] from stored data, or None when the key is absent / not a list. One entry per file."""
    if not isinstance(v, list): return None
    out, seen = [], set()
    for x in v:
        if isinstance(x, str): x = {"file": x}
        if not isinstance(x, dict) or not isinstance(x.get("file"), str) or not x["file"]: continue
        f = x["file"]
        if f.lower() in seen: continue
        seen.add(f.lower())
        alias = x.get("alias") if isinstance(x.get("alias"), str) else ""
        out.append({"file": f, "alias": alias.strip() or Path(f).stem})
    return out


def _clean_settings(meta: dict) -> dict:
    """The generation setup stored in a folder's metadata, validated field by field.
    Selected APIs: 'selected_apis' (new) — or migrated from the old 'api_rotation' list, else from a single
    old 'selected_api'. 'apis_defined' says whether the stored data spoke about APIs at all."""
    def _strs(v):
        return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []
    try:
        rounds = max(1, int(meta.get("rounds_count", 3)))
    except (TypeError, ValueError):
        rounds = 3
    size = meta.get("sel_size")
    wc = meta.get("wildcard_text")
    apis = _parse_selected_apis(meta.get("selected_apis"))
    defined = apis is not None
    if apis is None:
        old = _strs(meta.get("api_rotation"))
        if not old and isinstance(meta.get("selected_api"), str) and meta["selected_api"]:
            old = [meta["selected_api"]]
        apis = _parse_selected_apis(old) or []
        defined = bool(apis)
    return {
        "prompts": _strs(meta.get("prompts")),
        "selected_apis": apis,
        "apis_defined": defined,
        "rounds_count": rounds,
        "sel_size": size if isinstance(size, str) and size else _DEFAULT_SIZE,
        "wildcard_text": wc if isinstance(wc, str) else "",
    }


_API_INFO_CACHE: dict = {}


def _api_info(file: str) -> dict:
    """What a workflow file offers, for the API lists: exists / readable / has a prompt input / has a seed
    input / needs a source image. Uses detect_mapping (unchanged); cached per file + modification time."""
    p = API_DIR / file
    try:
        mt = p.stat().st_mtime
    except OSError:
        return {"exists": False, "error": "", "prompt": False, "seed": False, "needs_image": False}
    key = (file, mt)
    hit = _API_INFO_CACHE.get(key)
    if hit: return hit
    info = {"exists": True, "error": "", "prompt": False, "seed": False, "needs_image": False}
    try:
        with open(p, encoding="utf-8") as f: wf = json.load(f)
        m = detect_mapping(wf)
        info["prompt"] = any(pn["role"] in ("positive", "unknown") for pn in m["prompt_nodes"])
        info["seed"] = bool(m["seed_node"])
        info["needs_image"] = bool(m["has_image_input"])
    except Exception as ex:
        info["error"] = str(ex)
    _API_INFO_CACHE[key] = info
    return info


def _read_folder_state(folder: Path) -> dict:
    """Open a Working Folder: reconcile its metadata with the files that actually exist.
      file + metadata   -> metadata is used;
      file, no metadata -> shown as an 'imported' image (group: Other Images);
      metadata, no file -> dropped (no stale thumbnail);
      metadata missing / unreadable -> every image on disk is still listed, as imported.
    Metadata loss never means image loss. Returns the settings plus 'items' (runtime dicts that carry both
    an absolute 'path' and the relative 'file') and a few counters/flags."""
    files = _scan_images(folder)
    meta, corrupt = None, False
    gj = _gallery_file(folder)
    if gj.exists():
        try:
            meta = json.loads(gj.read_text(encoding="utf-8"))
            if not isinstance(meta, dict):
                raise ValueError("gallery.json is not an object")
        except Exception as ex:
            print(f"gallery.json unreadable ({ex}); rebuilding from the files in the folder")
            meta, corrupt = None, True
            try:                                         # keep what was there so it can be inspected
                shutil.copy2(gj, gj.with_name(f"gallery.corrupt-{int(time.time())}.json"))
            except OSError:
                pass
    items, seen, pruned = [], set(), 0
    for it in ((meta or {}).get("items") or []):
        if not isinstance(it, dict): continue
        fname = it.get("file")
        if not isinstance(fname, str): continue
        real = files.get(fname.lower())
        if real is None:
            pruned += 1; continue                        # file gone: forget its metadata
        if real.name.lower() in seen: continue
        seen.add(real.name.lower())
        prompt, api = it.get("prompt"), it.get("api")
        ts = it.get("ts")
        items.append({
            "path": str(real), "file": real.name,
            "source": it.get("source") if it.get("source") in ("generated", "imported") else "generated",
            "prompt": prompt if isinstance(prompt, str) else "",
            "api": api if isinstance(api, str) else "",
            "ts": ts if isinstance(ts, (int, float)) else 0,
        })
    imported = 0
    for key, real in sorted(files.items(), key=lambda kv: (_mtime(kv[1]), kv[0])):
        if key in seen: continue
        items.append({"path": str(real), "file": real.name, "source": "imported",
                      "prompt": "", "api": "", "ts": _mtime(real)})
        imported += 1
    st = _clean_settings(meta or {})
    st.update(items=items, had_meta=meta is not None, corrupt=corrupt, imported=imported, pruned=pruned)
    return st


def _mtime(p: Path) -> float:
    try: return p.stat().st_mtime
    except OSError: return 0.0


def _write_folder_state(folder: Path, prompts, items, selected_apis, rounds, size, wildcard) -> None:
    """Persist a Working Folder's state to <folder>/.gallery/gallery.json (atomic). Image entries use the
    relative file name only — never an absolute path."""
    d = folder / GALLERY_DIR_NAME
    fresh = not d.exists()
    payload = {
        "version": 1,
        "prompts": list(prompts),
        "selected_apis": [{"file": a["file"], "alias": a.get("alias") or Path(a["file"]).stem}
                          for a in selected_apis],
        "rounds_count": rounds,
        "sel_size": size,
        "wildcard_text": wildcard,
        "items": [{"file": it["file"], "source": it.get("source", "generated"), "prompt": it.get("prompt", ""),
                   "api": it.get("api", ""), "ts": it.get("ts", 0)} for it in items],
    }
    _atomic_write_json(_gallery_file(folder), payload)
    if fresh and os.name == "nt":                         # a dot-folder is not hidden on Windows by itself
        try:
            import ctypes
            ctypes.windll.kernel32.SetFileAttributesW(str(d), 0x02)
        except Exception:
            pass


def _legacy_items_for_folder(legacy_gallery, folder: Path) -> list:
    """Old absolute-path entries that physically live directly inside `folder`, as relative entries.
    Entries pointing elsewhere are left behind (they stay in the saved_gallery.legacy.json backup)."""
    out = []
    want = os.path.normcase(os.path.abspath(str(folder)))
    for it in (legacy_gallery if isinstance(legacy_gallery, list) else []):
        if not isinstance(it, dict) or not isinstance(it.get("path"), str): continue
        pth = Path(it["path"])
        if os.path.normcase(os.path.abspath(str(pth.parent))) != want: continue
        out.append({"file": pth.name, "source": "generated",
                    "prompt": it.get("prompt") if isinstance(it.get("prompt"), str) else "",
                    "api": it.get("api") if isinstance(it.get("api"), str) else "",
                    "ts": it.get("ts") if isinstance(it.get("ts"), (int, float)) else 0})
    return out

# ════════════════════════════════════════════════════════════════════════
async def main(page: ft.Page):
    page.title = f"{APP_NAME} {APP_VERSION}"
    _icon = APP_ROOT / "icons" / "gallery_generator.ico"
    if _icon.exists():
        page.window.icon = str(_icon)
    page.bgcolor = C["bg"]
    page.padding = 0; page.spacing = 0

    # ── State ──────────────────────────────────────────────────────────
    saved = _load()
    # Old builds kept everything (including the image list) in saved_gallery.json. Keep a backup of that
    # file once, then migrate: it is never deleted and never needed again afterwards.
    _is_legacy = any(k in saved for k in _LEGACY_KEYS)
    if _is_legacy and not LEGACY_BACKUP_PATH.exists():
        try: shutil.copy2(SAVE_PATH, LEGACY_BACKUP_PATH)
        except OSError: pass
    _draft: list = [saved.get("draft") if isinstance(saved.get("draft"), dict) else
                    (_clean_settings(saved) if _is_legacy else None)]   # setup typed before any folder exists
    _wf_str = saved.get("last_working_folder") or saved.get("output_folder") or ""
    _wf_path = Path(_wf_str) if _wf_str and Path(_wf_str).is_dir() else None
    if (_wf_path and _is_legacy and not _gallery_file(_wf_path).exists()
            and saved.get("output_folder")
            and os.path.normcase(os.path.abspath(saved["output_folder"])) == os.path.normcase(os.path.abspath(str(_wf_path)))):
        _lg = _clean_settings(saved)                      # first run after the upgrade: old state -> folder
        try:
            _write_folder_state(_wf_path, _lg["prompts"], _legacy_items_for_folder(saved.get("gallery"), _wf_path),
                                _lg["selected_apis"], _lg["rounds_count"], _lg["sel_size"], _lg["wildcard_text"])
            _draft[0] = None
        except Exception as _mex:
            print(f"legacy migration could not write the folder state: {_mex}")
    _st = _read_folder_state(_wf_path) if _wf_path else _clean_settings(_draft[0] or {})
    if not _wf_path: _st["items"] = []
    if not _st["apis_defined"] and saved.get("selected_api"):
        _st["selected_apis"] = _parse_selected_apis([saved["selected_api"]]) or []   # the old global single workflow
    prompts_list : list[str] = list(_st["prompts"])
    gallery_by_prompt: dict = {}  # prompt_text -> [items]; prompt "" = Other Images (imported files)
    for _item in _st["items"]:
        gallery_by_prompt.setdefault(_item.get("prompt", ""), []).append(_item)
    input_image  : list[str]  = [saved.get("input_image", "")] if saved.get("input_image") else []
    working_dir  : list[str]  = [str(_wf_path) if _wf_path else ""]      # the Working Folder
    # The APIs (workflow files) to generate with, in generation order — the one source of truth.
    # Each entry: {"file": real workflow file name (never renamed), "alias": display name}.
    selected_apis: list[dict] = [dict(x) for x in _st["selected_apis"]]
    rounds_count : list[int]  = [_st["rounds_count"]]   # rounds per prompt
    _sel_size    : list[str]  = [_st["sel_size"]]
    limit_count  : list[int]  = [0]
    wildcard_text: list[str]  = [_st["wildcard_text"]]
    _stop_event = asyncio.Event()
    _is_running: list[bool] = [False]

    def _all_gallery_items() -> list[dict]:
        items = []
        for lst in gallery_by_prompt.values():
            items.extend(lst)
        return items

    def _save_state():
        """Global preferences go to saved_gallery.json; everything that belongs to the work goes into the
        Working Folder. Always called AFTER the in-memory state was changed."""
        try:
            g = {"version": 2,
                 "last_working_folder": working_dir[0],
                 "input_image": input_image[0] if input_image else ""}   # kept for later; unused while hidden
            if not working_dir[0]:                        # no folder yet: keep the typed setup as a draft
                g["draft"] = {"prompts": prompts_list, "selected_apis": selected_apis,
                              "rounds_count": rounds_count[0], "sel_size": _sel_size[0],
                              "wildcard_text": wildcard_text[0]}
            _save(g)
            if working_dir[0]:
                _write_folder_state(Path(working_dir[0]), prompts_list, _all_gallery_items(), selected_apis,
                                    rounds_count[0], _sel_size[0], wildcard_text[0])
        except Exception as ex:
            print(f"save error: {ex}")
            try:
                status_lbl.value = f"⚠ Could not save gallery data: {ex}"; page.update()
            except Exception:
                pass

    if _is_legacy or saved.get("selected_api") or (_wf_path and (_st["corrupt"] or not _st["had_meta"] or _st["imported"] or _st["pruned"])):
        _save_state()        # make the migration / reconciliation durable (old file already backed up)

    # ── HEADER ────────────────────────────────────────────────────────
    header = ft.Container(
        content=ft.Row([
            ft.Row([
                ft.Text("Gallery", size=15, weight=ft.FontWeight.W_700, color=C["text1"]),
                ft.Container(
                    content=ft.Text("Generator", size=10, weight=ft.FontWeight.W_700, color=C["accent"]),
                    bgcolor=C["accent_lt"], border_radius=br(4),
                    padding=po(left=7, right=7, top=2, bottom=2)),
            ], spacing=6),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        bgcolor=C["surface"], padding=po(left=20, right=20, top=11, bottom=11),
        border=ft.Border(bottom=ft.BorderSide(1, C["border"])), height=46,
    )

    # ══════════════════════════════════════════════════════════════════
    # LEFT — Gallery grid
    # ══════════════════════════════════════════════════════════════════

    # Gallery is displayed as a column grouped by prompt
    gallery_col = ft.Column(
        spacing=0, expand=True, scroll=ft.ScrollMode.AUTO,
    )

    # ── Selection state (transient, UI-only — never written to any gallery data file) ───────────
    _sel: set = set()                    # paths of the selected thumbnails
    _sel_anchor: list = [None]           # anchor path for Shift-range selection
    _sel_focus: list = [None]            # primary / last-clicked path
    _mods = {"ctrl": False, "shift": False}      # modifier keys currently held (see KeyboardListener below)
    _typing: set = set()                 # ids of text fields that currently own keyboard focus
    _confirm_open = [False]
    _last_down: list = [None, 0.0]       # (path, time) of the previous tap-down, to spot a double click
    _kb_listener: list = [None]          # set once the app is assembled; used to take focus off text fields

    def _pl(n: int, word: str) -> str:
        return f"{n} {word}" + ("" if n == 1 else "s")

    def _display_list() -> list[dict]:
        """Collect all items and order them by prompt order — the one canonical visual order, shared by
        Shift-range selection, Ctrl+A, viewer navigation and deletion."""
        result = []
        for pt in _ordered_prompts():
            result.extend(gallery_by_prompt.get(pt, []))
        return result

    def _item_by_path(path: str):
        return next((x for x in _display_list() if x.get("path") == path), None)

    # ── Lightbox / viewer ─────────────────────────────────────────────
    lightbox_img = ft.Image(src="", fit=ft.BoxFit.CONTAIN, expand=True)
    lightbox_prompt_lbl = ft.Text("", size=11, color="#9A9AA6",
                                  max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
    lightbox_counter_lbl = ft.Text("", size=11, color="#9A9AA6",
                                   text_align=ft.TextAlign.CENTER)
    _lightbox_item: list[dict] = [{}]
    _lightbox_idx:  list[int]  = [0]   # index in display order

    # ── Viewer zoom: click = 100% around the pointer, click again = back to fit ──────────────────
    # While zoomed the picture is shown at its real pixel size inside the (clipped) image area, positioned so
    # the point that was under the pointer stays under it; dragging pans. A magnifier badge follows the
    # pointer over the image (the system cursor is also a zoom cursor where the platform has one).
    _area = [0.0, 0.0]                         # size of the image area, logical px
    _zoom = {"on": False, "iw": 0.0, "ih": 0.0, "pos": (0.0, 0.0)}
    _nat_cache: dict = {}
    zoom_img = ft.Image(src="", fit=ft.BoxFit.FILL, left=0, top=0, width=10, height=10, visible=False)
    fit_box = ft.Container(content=lightbox_img, left=0, top=0, right=0, bottom=0, alignment=ft.Alignment(0, 0))
    magnifier_icon = ft.Icon(ft.Icons.ZOOM_IN_ROUNDED, size=18, color="#FFFFFF")
    magnifier = ft.Container(content=magnifier_icon, width=30, height=30, border_radius=br(15),
                             bgcolor=ft.Colors.with_opacity(0.72, "#000000"), alignment=ft.Alignment(0, 0),
                             left=0, top=0, visible=False)

    def _natural_size(path: str):
        d = _nat_cache.get(path)
        if d: return d
        try:
            from PIL import Image
            with Image.open(path) as im:
                d = im.size
        except Exception:
            return None
        _nat_cache[path] = d
        return d

    def _dpr() -> float:
        try:
            return max(0.5, float(page.media.device_pixel_ratio or 1.0))
        except Exception:
            return 1.0

    def _clamp_pan(left: float, top: float) -> tuple[float, float]:
        aw, ah = _area; iw, ih = _zoom["iw"], _zoom["ih"]
        left = (aw - iw) / 2 if iw <= aw else max(aw - iw, min(0.0, left))
        top = (ah - ih) / 2 if ih <= ah else max(ah - ih, min(0.0, top))
        return left, top

    def _zoom_reset():
        _zoom["on"] = False
        zoom_img.visible = False; fit_box.visible = True
        viewer_gd.mouse_cursor = ft.MouseCursor.ZOOM_IN
        magnifier_icon.icon = ft.Icons.ZOOM_IN_ROUNDED

    def _zoom_enter(x: float, y: float):
        item = _lightbox_item[0]
        nat = _natural_size(item.get("path", "")) if item else None
        aw, ah = _area
        if not nat or aw <= 0 or ah <= 0: return
        nw, nh = nat
        f = min(aw / nw, ah / nh)                                    # how the fitted picture maps to the area
        ox, oy = (aw - nw * f) / 2, (ah - nh * f) / 2
        px = min(max((x - ox) / f, 0.0), float(nw))                  # image pixel under the pointer
        py = min(max((y - oy) / f, 0.0), float(nh))
        sc = 1.0 / _dpr()                                            # 100%: one image pixel = one device pixel
        _zoom.update(on=True, iw=nw * sc, ih=nh * sc)
        zoom_img.src = lightbox_img.src
        zoom_img.width, zoom_img.height = nw * sc, nh * sc
        zoom_img.left, zoom_img.top = _clamp_pan(x - px * sc, y - py * sc)
        zoom_img.visible = True; fit_box.visible = False
        viewer_gd.mouse_cursor = ft.MouseCursor.ZOOM_OUT
        magnifier_icon.icon = ft.Icons.ZOOM_OUT_ROUNDED

    def _viewer_tap_down(e):
        _zoom["pos"] = (float(e.local_position.x), float(e.local_position.y))

    def _viewer_tap(e):
        if _zoom["on"]: _zoom_reset()
        else: _zoom_enter(*_zoom["pos"])
        page.update()

    def _move_magnifier(x: float, y: float):
        aw, ah = _area
        magnifier.left = max(0.0, min(x + 16, aw - 30)); magnifier.top = max(0.0, min(y + 16, ah - 30))
        magnifier.visible = True

    def _viewer_hover(e):
        _move_magnifier(float(e.local_position.x), float(e.local_position.y))
        page.update()

    def _viewer_exit(e):
        magnifier.visible = False; page.update()

    def _viewer_pan(e):
        if not _zoom["on"]: return
        zoom_img.left, zoom_img.top = _clamp_pan(zoom_img.left + float(e.local_delta.x),
                                                 zoom_img.top + float(e.local_delta.y))
        _move_magnifier(float(e.local_position.x), float(e.local_position.y))
        page.update()

    def _area_resized(e):
        _area[0], _area[1] = float(e.width), float(e.height)
        if _zoom["on"]:
            zoom_img.left, zoom_img.top = _clamp_pan(zoom_img.left, zoom_img.top)
            page.update()

    viewer_gd = ft.GestureDetector(
        content=ft.Stack([fit_box, zoom_img, magnifier], expand=True),
        on_tap_down=_viewer_tap_down, on_tap=_viewer_tap, on_hover=_viewer_hover, on_exit=_viewer_exit,
        on_pan_update=_viewer_pan, hover_interval=25, mouse_cursor=ft.MouseCursor.ZOOM_IN, expand=True)
    viewer_area = ft.Container(content=viewer_gd, expand=True, clip_behavior=ft.ClipBehavior.HARD_EDGE,
                               on_size_change=_area_resized)

    def _lightbox_size() -> tuple[int, int]:
        """Viewer size follows the window (about 90% x 88%), clamped so it still fits 1366x768."""
        try:
            pw = float(page.width or page.window.width or 1280)
            ph = float(page.height or page.window.height or 720)
        except Exception:
            pw, ph = 1280.0, 720.0
        return int(max(480, min(pw * 0.90, 1900))), int(max(400, min(ph * 0.88, 1200)))

    def close_lightbox(e=None):
        lightbox_dlg.open = False; page.update()

    def _purge_caches(path: str):
        """Drop cached thumbnail / full-size data URIs of a deleted file, so no stale image survives
        (the cache key includes the mtime, but a path can be re-used by a later generation)."""
        for cache in (_SRC_CACHE, _THUMB_CACHE):
            for k in [k for k in cache if k and k[0] == path]:
                cache.pop(k, None)

    def _delete_items(items: list) -> tuple[int, int]:
        """The single deletion path: unlink the file, forget it in gallery state, clean caches, persist.
        A file that is already missing still leaves the gallery; a file that cannot be removed stays
        (and is counted as failed) while the rest of the batch carries on."""
        deleted = failed = 0
        for item in list(items):
            path = item.get("path", "")
            try:
                Path(path).unlink(missing_ok=True)
            except Exception as ex:
                print(f"delete error: {path}: {ex}")
                failed += 1
                continue
            for pt in list(gallery_by_prompt):
                lst = gallery_by_prompt[pt]
                if item in lst:
                    lst.remove(item)
                    if not lst:
                        del gallery_by_prompt[pt]
                    break
            _purge_caches(path)
            _sel.discard(path)
            if _sel_anchor[0] == path: _sel_anchor[0] = None
            if _sel_focus[0] == path: _sel_focus[0] = None
            deleted += 1
        if deleted:
            _save_state()
        _update_gallery_count()
        rebuild_gallery(keep_scroll=True)
        return deleted, failed

    def _report_delete(deleted: int, failed: int):
        if not failed: return
        page.show_dialog(ft.SnackBar(
            content=ft.Text(f"Deleted {deleted}; {failed} could not be removed (file in use or read-only)."),
            duration=4000))

    def _request_delete(items: list, after=None):
        """Ask first, then delete. Permanent deletion is never silent, in the grid or in the viewer.
        A fresh dialog per request: Flet keeps a closed dialog in its stack until the client reports the
        dismissal, so re-showing one shared instance too quickly raises 'Dialog is already opened'."""
        items = [x for x in items if x]
        if not items or _confirm_open[0]: return
        n = len(items)

        def _close(run: bool):
            if not _confirm_open[0]: return
            _confirm_open[0] = False
            page.pop_dialog()
            if run:
                d, f = _delete_items(items)
                if after: after(d, f)
                _report_delete(d, f)

        dlg = ft.AlertDialog(
            modal=True, bgcolor=C["surface"],
            title=ft.Text("Delete this image?" if n == 1 else f"Delete {n} images?",
                          size=14, weight=ft.FontWeight.W_700, color=C["text1"]),
            content=ft.Container(width=360, content=ft.Text(
                "This file will be permanently deleted from the Working Folder." if n == 1 else
                "These files will be permanently deleted from the Working Folder.",
                size=12, color=C["text2"])),
            actions=[
                ft.TextButton("Cancel", on_click=lambda e: _close(False),
                              style=ft.ButtonStyle(color=C["text2"])),
                ft.FilledButton("Delete", on_click=lambda e: _close(True), bgcolor=C["red"],
                                style=ft.ButtonStyle(color="#FFFFFF")),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            on_dismiss=lambda e: _confirm_open.__setitem__(0, False),
        )
        _confirm_open[0] = True
        page.show_dialog(dlg)

    def delete_current(e=None):
        """Viewer delete: the viewer stays open on the next surviving image (previous one if the last
        was deleted); it closes only when nothing is left."""
        item = _lightbox_item[0]
        idx = _lightbox_idx[0]

        def _after(deleted: int, failed: int):
            disp = _display_list()
            if not disp:
                close_lightbox(); return
            _load_lightbox_at(min(idx, len(disp) - 1))
        _request_delete([item], after=_after)

    def _load_lightbox_at(idx: int):
        disp = _display_list()
        if not disp: return
        idx = max(0, min(idx, len(disp) - 1))
        _lightbox_idx[0] = idx
        item = disp[idx]
        _lightbox_item[0] = item
        _zoom_reset()                                   # every picture opens fitted, not zoomed
        lightbox_img.src = _to_src(item["path"])
        lightbox_prompt_lbl.value = item.get("prompt", "")
        lightbox_counter_lbl.value = f"{idx + 1} / {len(disp)}"
        nav_prev_btn.disabled = nav_next_btn.disabled = len(disp) <= 1      # navigation wraps around
        page.update()

    def nav_prev(e=None):
        n = len(_display_list())
        if n: _load_lightbox_at((_lightbox_idx[0] - 1) % n)

    def nav_next(e=None):
        n = len(_display_list())
        if n: _load_lightbox_at((_lightbox_idx[0] + 1) % n)

    # ── open files/folders using the system app ──────────────────────────
    def _reveal(path: Path):
        import subprocess, platform
        if not path.exists(): return
        system = platform.system()
        try:
            if system == "Windows":
                subprocess.run(["explorer", "/select,", str(path)])
            elif system == "Darwin":
                subprocess.run(["open", "-R", str(path)])
            else:  # Linux
                subprocess.run(["xdg-open", str(path.parent)])
        except Exception as ex:
            print(f"show_in_explorer error: {ex}")

    def show_in_explorer(e=None):
        _reveal(Path(_lightbox_item[0].get("path", "")))

    def show_selected_in_explorer(e=None):
        first = next((x for x in _display_list() if x.get("path") in _sel), None)
        if first: _reveal(Path(first["path"]))

    def open_by_system(e=None):
        import subprocess, platform, os
        item = _lightbox_item[0]
        path = Path(item.get("path", ""))
        if not path.exists(): return
        system = platform.system()
        try:
            if system == "Windows":
                os.startfile(str(path))
            elif system == "Darwin":
                subprocess.run(["open", str(path)])
            else:
                subprocess.run(["xdg-open", str(path)])
        except Exception as ex:
            print(f"open_by_system error: {ex}")

    _DARK = "#16161A"
    nav_prev_btn = ft.IconButton(
        icon=ft.Icons.CHEVRON_LEFT_ROUNDED, icon_color="#FFFFFF", icon_size=30,
        on_click=nav_prev, width=48, height=48, tooltip="Previous (←)",
        bgcolor="#2A2A31", style=ft.ButtonStyle(shape=ft.CircleBorder()),
    )
    nav_next_btn = ft.IconButton(
        icon=ft.Icons.CHEVRON_RIGHT_ROUNDED, icon_color="#FFFFFF", icon_size=30,
        on_click=nav_next, width=48, height=48, tooltip="Next (→)",
        bgcolor="#2A2A31", style=ft.ButtonStyle(shape=ft.CircleBorder()),
    )

    def _viewer_btn(icon, label, on_click):
        return ft.OutlinedButton(
            content=ft.Row([ft.Icon(icon, size=15, color="#C8C8D0"),
                            ft.Text(label, size=11, color="#C8C8D0")], tight=True, spacing=5),
            on_click=on_click, height=34,
            style=ft.ButtonStyle(side=ft.BorderSide(1, "#3A3A44")),
        )

    lightbox_body = ft.Container(
        content=ft.Column([
            ft.Row([
                lightbox_counter_lbl,
                ft.Container(expand=True),
                ft.IconButton(icon=ft.Icons.CLOSE_ROUNDED, icon_color="#C8C8D0",
                              on_click=close_lightbox, icon_size=20, tooltip="Close (Esc)"),
            ], height=36),
            ft.Row([
                nav_prev_btn,
                viewer_area,
                nav_next_btn,
            ], expand=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(content=lightbox_prompt_lbl, padding=po(left=4, right=4, top=2, bottom=0)),
            ft.Row([
                ft.Row([
                    _viewer_btn(ft.Icons.FOLDER_OPEN_ROUNDED, "Show in Explorer", show_in_explorer),
                    _viewer_btn(ft.Icons.OPEN_IN_NEW_ROUNDED, "Open", open_by_system),
                ], spacing=8),
                ft.OutlinedButton(
                    content=ft.Row([ft.Icon(ft.Icons.DELETE_OUTLINE_ROUNDED, size=16, color="#F28B82"),
                                    ft.Text("Delete", size=12, color="#F28B82")], tight=True, spacing=5),
                    on_click=delete_current, height=34, tooltip="Delete (Del)",
                    style=ft.ButtonStyle(side=ft.BorderSide(1, "#5A3A3A")),
                ),
            ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=8, expand=True),
        bgcolor=_DARK, padding=po(left=14, right=14, top=6, bottom=12),
        width=900, height=640,
    )

    lightbox_dlg = ft.AlertDialog(
        modal=True, bgcolor=_DARK,
        content=lightbox_body,
        content_padding=0, inset_padding=ft.Padding.all(16),
        shape=ft.RoundedRectangleBorder(radius=12),
        open=False,
    )
    page.overlay.append(lightbox_dlg)

    def open_lightbox(item: dict):
        disp = _display_list()
        idx = next((i for i, x in enumerate(disp) if x is item), 0)
        lightbox_body.width, lightbox_body.height = _lightbox_size()
        _load_lightbox_at(idx)
        lightbox_dlg.open = True
        page.update()

    def _on_resize(e=None):
        if lightbox_dlg.open:
            lightbox_body.width, lightbox_body.height = _lightbox_size()
            page.update()
        if _group_rows and abs(_avail_width() - _layout_w[0]) > 8:      # tiles re-flow to the new width
            _relayout()
            page.update()

    page.on_resize = _on_resize

    # ── Thumbnails ────────────────────────────────────────────────────
    _tiles: dict = {}                    # path -> (tile container, check badge)
    _hovered: set = set()

    def _style_tile(path: str):
        ref = _tiles.get(path)
        if not ref: return
        tile, check = ref
        sel = path in _sel
        hov = path in _hovered
        tile.border = ft.Border.all(2, C["accent"] if sel else (C["border2"] if hov else C["border"]))
        tile.bgcolor = C["accent_lt"] if sel else ("#EDEDF1" if hov else C["panel"])
        check.opacity = 1.0 if sel else 0.0

    def _selection_changed(paths):
        for p in paths:
            _style_tile(p)
        _update_gallery_count()
        page.update()

    def _select_only(path: str):
        old = set(_sel)
        _sel.clear(); _sel.add(path)
        _sel_anchor[0] = path; _sel_focus[0] = path
        _selection_changed(old | _sel)

    def _toggle(path: str):
        old = set(_sel)
        if path in _sel: _sel.discard(path)
        else: _sel.add(path)
        _sel_anchor[0] = path; _sel_focus[0] = path
        _selection_changed(old | _sel | {path})

    def _select_range(path: str, additive: bool):
        order = [x["path"] for x in _display_list()]
        a = _sel_anchor[0]
        if a not in order or path not in order:
            _select_only(path); return
        i, j = sorted((order.index(a), order.index(path)))
        rng = set(order[i:j + 1])
        old = set(_sel)
        new = (_sel | rng) if additive else rng
        _sel.clear(); _sel.update(new)
        _sel_focus[0] = path                      # the anchor stays where the range started
        _selection_changed(old | _sel)

    def _select_all():
        old = set(_sel)
        order = [x["path"] for x in _display_list()]
        _sel.clear(); _sel.update(order)
        if order:
            _sel_anchor[0] = order[0]
        _selection_changed(old | _sel)

    def _clear_selection():
        if not _sel: return
        old = set(_sel)
        _sel.clear(); _sel_anchor[0] = None; _sel_focus[0] = None
        _selection_changed(old)

    async def _focus_anchor():
        try:                                       # take keyboard focus off any text field
            if _kb_listener[0] is not None:
                await _kb_listener[0].focus()
        except Exception:
            pass

    def _on_tile_down(e, path: str):
        """Pointer down = select (immediate, no double-click delay). The second press of a double click
        is ignored here so a Ctrl double-click does not toggle twice. Synchronous on purpose: selection
        must be applied in event order, before any later key press (Delete, Ctrl+A ...) is handled."""
        now = time.time()
        second = _last_down[0] == path and (now - _last_down[1]) < 0.45
        _last_down[0], _last_down[1] = (None, 0.0) if second else (path, now)
        ctrl_held, shift_held = _mods["ctrl"], _mods["shift"]      # read before focus moves
        if not second:
            if shift_held:
                _select_range(path, additive=ctrl_held)
            elif ctrl_held:
                _toggle(path)
            else:
                _select_only(path)
        page.run_task(_focus_anchor)

    def _on_tile_double(e, path: str):
        item = _item_by_path(path)
        if not item: return
        if path not in _sel or len(_sel) != 1:
            _select_only(path)
        open_lightbox(item)

    _dims_cache: dict = {}

    def _thumb_dims(path: str) -> tuple[int, int]:
        """Pixel size the thumbnail is drawn at (longest side 200, as _to_thumb_src produces). Tiles get an
        explicit size so selecting one never depends on the image re-measuring itself after a rebuild."""
        d = _dims_cache.get(path)
        if d: return d
        try:
            from PIL import Image
            with Image.open(path) as im:
                w, h = im.size
            k = 200.0 / max(w, h, 1)
            d = (max(24, int(w * k)), max(24, int(h * k))) if k < 1 else (max(24, w), max(24, h))
        except Exception:
            d = (120, 120)
        _dims_cache[path] = d
        return d

    def _make_thumb_tile(item: dict) -> ft.ContextMenu:
        path = item["path"]
        exists = Path(path).exists()
        tw, th = _thumb_dims(path) if exists else (120, 120)
        check = ft.Container(
            content=ft.Icon(ft.Icons.CHECK_ROUNDED, size=13, color="#FFFFFF"),
            width=20, height=20, bgcolor=C["accent"], border_radius=br(10),
            alignment=ft.Alignment(0, 0), right=6, top=6, opacity=1.0 if path in _sel else 0.0,
        )
        body = (ft.Image(src=_to_thumb_src(path), fit=ft.BoxFit.COVER, border_radius=br(6),
                         width=tw, height=th)
                if exists else
                ft.Container(content=ft.Icon(ft.Icons.BROKEN_IMAGE_OUTLINED, color=C["text3"], size=24),
                             alignment=ft.Alignment(0, 0), width=tw, height=th))
        tile = ft.Container(
            content=ft.Stack([body, check], width=tw, height=th),
            padding=ft.Padding.all(2), border_radius=br(8),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            bgcolor=C["panel"], border=ft.Border.all(2, C["border"]),
        )
        _tiles[path] = (tile, check)
        _style_tile(path)

        def _hover(e, _p=path):
            if str(e.data).lower() == "true": _hovered.add(_p)
            else: _hovered.discard(_p)
            _style_tile(_p); page.update()
        tile.on_hover = _hover
        def _down(e, _p=path):
            _on_tile_down(e, _p)

        def _double(e, _p=path):
            _on_tile_double(e, _p)
        def _secondary_down(e, _p=path):
            """Right-press: like a file browser, a card that is not part of the selection becomes the
            selection; a card inside a multi-selection keeps it, so the menu acts on all of them."""
            if _p not in _sel:
                _select_only(_p)
            page.run_task(_focus_anchor)

        def _targets(_p=path) -> list:
            return [x for x in _display_list() if x.get("path") in _sel] if _p in _sel else                    [x for x in [_item_by_path(_p)] if x]

        def _menu_view(e, _p=path):
            item = _item_by_path(_p)
            if item:
                if _p not in _sel or len(_sel) != 1: _select_only(_p)
                open_lightbox(item)

        def _menu_reveal(e, _p=path):
            _reveal(Path(_p))

        def _menu_delete(e, _p=path):
            _request_delete(_targets(_p))

        menu = [
            ft.PopupMenuItem(content=ft.Text("View", size=12), icon=ft.Icons.OPEN_IN_FULL_ROUNDED, on_click=_menu_view),
            ft.PopupMenuItem(content=ft.Text("Show in Explorer", size=12), icon=ft.Icons.FOLDER_OPEN_ROUNDED,
                             on_click=_menu_reveal),
            ft.PopupMenuItem(),                                               # divider
            ft.PopupMenuItem(content=ft.Text("Delete", size=12, color=C["red"]),
                             icon=ft.Icons.DELETE_OUTLINE_ROUNDED, on_click=_menu_delete),
        ]
        gd = ft.GestureDetector(
            content=tile, mouse_cursor=ft.MouseCursor.CLICK,
            on_tap_down=_down, on_double_tap=_double, on_secondary_tap_down=_secondary_down,
        )
        return ft.ContextMenu(content=gd, secondary_items=menu, tertiary_trigger=None)

    # Gallery layout. Rows are laid out HERE (not by a wrapping Row) with fixed sizes, so the rectangle of
    # every tile is known exactly — that is what makes drag (marquee) selection possible.
    _HDR_H, _PAD_T, _PAD_B, _SIDE, _GAP, _RIGHT_W = 32, 8, 12, 12, 6, 290
    _group_boxes: dict = {}            # prompt_text -> ft.Container (whole group)
    _group_rows: dict = {}             # prompt_text -> ft.Column (the rows of tiles)
    _prompt_count_lbls: dict = {}      # prompt_text -> ft.Text ("8 images")
    _tile_ctrl: dict = {}              # path -> GestureDetector (kept so a relayout reuses the controls)
    _rects: dict = {}                  # path -> (x, y, w, h) in gallery content coordinates
    _layout_w = [0]                    # content width the current rows were planned for
    _content_h = [0]                   # total height of the laid-out gallery content

    def _avail_width() -> int:
        try:
            pw = float(page.width or page.window.width or 1280)
        except Exception:
            pw = 1280.0
        return max(240, int(pw - _RIGHT_W - 2 * _SIDE - 20))      # 20px slack: scrollbar / border

    def _tile_outer(path: str) -> tuple[int, int]:
        w, h = _thumb_dims(path) if Path(path).exists() else (120, 120)
        return w + 8, h + 8                                         # + 2px padding + 2px border each side

    def _plan_rows(paths: list, avail: int) -> list:
        rows, cur, cur_w = [], [], 0
        for pth in paths:
            w = _tile_outer(pth)[0]
            if cur and cur_w + _GAP + w > avail:
                rows.append(cur); cur, cur_w = [], 0
            cur_w += (_GAP if cur else 0) + w
            cur.append(pth)
        if cur: rows.append(cur)
        return rows

    def _make_prompt_row(prompt_text: str) -> ft.Container:
        rows_col = ft.Column(spacing=_GAP)
        _group_rows[prompt_text] = rows_col
        count_lbl = ft.Text("", size=10, color=C["text3"])
        _prompt_count_lbls[prompt_text] = count_lbl
        c = ft.Container(
            content=ft.Column([
                ft.Container(
                    content=ft.Row([
                        ft.Text(prompt_text or "Other Images", size=11, color=C["text1"],
                                weight=ft.FontWeight.W_600,
                                expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS,
                                tooltip=prompt_text or "Other Images"),
                        count_lbl,
                    ], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=po(left=_SIDE, right=_SIDE), height=_HDR_H, alignment=ft.Alignment(-1, 0),
                ),
                ft.Container(height=1, bgcolor=C["border"], margin=po(left=_SIDE, right=_SIDE)),
                ft.Container(
                    content=rows_col,
                    padding=po(left=_SIDE, right=_SIDE, top=_PAD_T, bottom=_PAD_B),
                ),
            ], spacing=0),
        )
        _group_boxes[prompt_text] = c
        return c

    def _ordered_prompts() -> list:
        """Canonical group order: prompts in list order, then any other prompt, then 'Other Images' (the
        group with the empty prompt: files found in the folder that this app did not generate)."""
        named = [p for p in prompts_list if p in gallery_by_prompt and p != ""]
        named += [p for p in gallery_by_prompt if p not in prompts_list and p != ""]
        out = list(dict.fromkeys(named))
        if "" in gallery_by_prompt:
            out.append("")
        return out

    def _relayout():
        """(Re)plan every group's rows for the current width, refill the row controls from the existing
        tile controls and recompute each tile's rectangle."""
        avail = _avail_width()
        _layout_w[0] = avail
        _rects.clear()
        y = 0
        for pt in _ordered_prompts():
            items = gallery_by_prompt.get(pt, [])
            if not items or pt not in _group_rows: continue
            rows = _plan_rows([x["path"] for x in items], avail)
            _group_rows[pt].controls = [
                ft.Row([_tile_ctrl[pth] for pth in row], spacing=_GAP,
                       vertical_alignment=ft.CrossAxisAlignment.START) for row in rows]
            y += _HDR_H + 1 + _PAD_T
            for ri, row in enumerate(rows):
                x, row_h = _SIDE, max(_tile_outer(pth)[1] for pth in row)
                for pth in row:
                    w, h = _tile_outer(pth)
                    _rects[pth] = (x, y, w, h)
                    x += w + _GAP
                y += row_h + (_GAP if ri < len(rows) - 1 else 0)
            y += _PAD_B
        _content_h[0] = y

    def _update_group_counts():
        for pt, lbl in _prompt_count_lbls.items():
            lbl.value = _pl(len(gallery_by_prompt.get(pt, [])), "image")

    def rebuild_gallery(keep_scroll: bool = False):
        """Rebuild every thumbnail. keep_scroll=True (used after deleting) puts the viewport back where it
        was, clamped to the new content height, and keeps the marquee's scroll offset in step."""
        keep = _scroll[0] if keep_scroll else None
        gallery_col.controls.clear()
        _group_boxes.clear(); _group_rows.clear(); _prompt_count_lbls.clear()
        _tile_ctrl.clear(); _tiles.clear(); _hovered.clear(); _rects.clear()
        all_items = _all_gallery_items()
        _sel.intersection_update({x["path"] for x in all_items})     # forget selection of vanished items
        if not all_items:
            gallery_col.controls.append(
                ft.Container(
                    content=ft.Column([
                        ft.Icon(ft.Icons.PHOTO_LIBRARY_OUTLINED, size=40, color=C["text3"]),
                        ft.Text("No images yet", size=13, color=C["text2"], weight=ft.FontWeight.W_600),
                        ft.Text("Generated images will appear here.", size=11, color=C["text3"]),
                    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=6),
                    alignment=ft.Alignment(0, 0),
                    height=220,
                )
            )
        else:
            for pt in _ordered_prompts():
                items = gallery_by_prompt.get(pt, [])
                if not items: continue
                for item in items:
                    _tile_ctrl[item["path"]] = _make_thumb_tile(item)
                gallery_col.controls.append(_make_prompt_row(pt))
            _relayout()
            _update_group_counts()
        _update_gallery_count()
        page.update()
        if keep is not None:
            try:
                vh = float(page.height or page.window.height or 720) - 92        # header + top bar
            except Exception:
                vh = 600.0
            target = max(0.0, min(keep, _content_h[0] - vh))
            _scroll[0] = target
            page.run_task(_scroll_to, target)

    def gallery_add_one(prompt_text: str, item: dict):
        """Add a thumbnail to the group for that prompt (the current selection is left untouched)."""
        gallery_by_prompt.setdefault(prompt_text, []).append(item)
        _tile_ctrl[item["path"]] = _make_thumb_tile(item)
        if prompt_text not in _group_rows:
            if not _group_boxes:
                gallery_col.controls.clear()                 # remove the empty state
            gallery_col.controls.insert(min(_ordered_prompts().index(prompt_text), len(gallery_col.controls)),
                                        _make_prompt_row(prompt_text))
        _relayout()
        _update_group_counts()
        _update_gallery_count()
        page.update()

    gallery_count_lbl = ft.Text("", size=11, color=C["text2"])
    gallery_sel_lbl = ft.Text("", size=11, color=C["accent"], weight=ft.FontWeight.W_600, visible=False)

    def _delete_selected(e=None):
        _request_delete([x for x in _display_list() if x.get("path") in _sel])

    show_sel_btn = ft.OutlinedButton(
        content=ft.Row([ft.Icon(ft.Icons.FOLDER_OPEN_ROUNDED, size=14, color=C["text2"]),
                        ft.Text("Show in Explorer", size=11, color=C["text2"])], tight=True, spacing=5),
        on_click=show_selected_in_explorer, height=30, visible=False,
        style=ft.ButtonStyle(side=ft.BorderSide(1, C["border2"])),
    )
    delete_sel_btn = ft.OutlinedButton(
        content=ft.Row([ft.Icon(ft.Icons.DELETE_OUTLINE_ROUNDED, size=14, color=C["red"]),
                        ft.Text("Delete", size=11, color=C["red"])], tight=True, spacing=5),
        on_click=_delete_selected, height=30, visible=False, tooltip="Delete selected (Del)",
        style=ft.ButtonStyle(side=ft.BorderSide(1, C["red"])),
    )

    def _update_gallery_count():
        total = sum(len(v) for v in gallery_by_prompt.values())
        gallery_count_lbl.value = f"{_pl(total, 'image')} · {_pl(len(gallery_by_prompt), 'prompt')}"
        n = len(_sel)
        gallery_sel_lbl.value = f"{n} selected"
        gallery_sel_lbl.visible = show_sel_btn.visible = delete_sel_btn.visible = n > 0

    gallery_top_bar = ft.Container(
        content=ft.Row([
            gallery_count_lbl,
            ft.Container(expand=True),
            gallery_sel_lbl,
            show_sel_btn,
            delete_sel_btn,
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=po(left=16, right=16, top=8, bottom=8), height=46,
        bgcolor=C["surface"],
        border=ft.Border(bottom=ft.BorderSide(1, C["border"])),
    )

    # Marquee selection: drag on the gallery draws a rectangle and selects every tile it touches. Tile
    # rectangles come from _rects (content coordinates); the scroll offset converts pointer positions.
    _scroll = [0.0]
    _mq = {"on": False, "x0": 0.0, "y0": 0.0, "x": 0.0, "y": 0.0, "base": set()}   # y0 / y: content coords
    marquee = ft.Container(
        left=0, top=0, width=0, height=0, visible=False,
        bgcolor=ft.Colors.with_opacity(0.14, C["accent"]),
        border=ft.Border.all(1, C["accent"]), border_radius=br(2),
    )

    def _marquee_apply():
        x0, x1 = sorted((_mq["x0"], _mq["x"]))
        y0, y1 = sorted((_mq["y0"], _mq["y"]))
        marquee.left, marquee.top = x0, y0 - _scroll[0]
        marquee.width, marquee.height = max(1.0, x1 - x0), max(1.0, y1 - y0)
        marquee.visible = True
        hits = {pth for pth, (rx, ry, rw, rh) in _rects.items()
                if rx < x1 and rx + rw > x0 and ry < y1 and ry + rh > y0}
        new = set(_mq["base"]) | hits
        old = set(_sel)
        if new != old:
            _sel.clear(); _sel.update(new)
            for pth in old ^ new:
                _style_tile(pth)
            _update_gallery_count()
        page.update()

    def _on_gallery_scroll(e):
        try:
            _scroll[0] = float(e.pixels)
        except Exception:
            return
        if _mq["on"]:
            _mq["y"] = _mq["last_vy"] + _scroll[0]
            _marquee_apply()
    gallery_col.on_scroll = _on_gallery_scroll
    _mq["last_vy"] = 0.0

    async def _scroll_to(offset: float):
        try:
            await gallery_col.scroll_to(offset=offset, duration=0)
        except Exception:
            pass

    async def _autoscroll(dy: float):
        try:
            await gallery_col.scroll_to(delta=dy, duration=0)
        except Exception:
            pass

    def _pan_down(e):
        """The press point: the rectangle must start where the button went down, not where the drag was
        recognised a few pixels later (or, for a fast flick, already far away)."""
        _mq["down"] = (float(e.local_position.x), float(e.local_position.y))

    def _pan_start(e):
        _mq["on"] = True
        dx, dy = _mq.get("down") or (float(e.local_position.x), float(e.local_position.y))
        _mq["x0"] = _mq["x"] = dx
        vy = dy
        _mq["y0"] = _mq["y"] = vy + _scroll[0]; _mq["last_vy"] = vy
        _mq["base"] = set(_sel) if (_mods["ctrl"] or _mods["shift"]) else set()
        if not _mq["base"] and _sel:                      # a plain drag replaces the old selection
            old = set(_sel); _sel.clear()
            for pth in old: _style_tile(pth)
        page.run_task(_focus_anchor)

    def _pan_update(e):
        if not _mq["on"]: return
        _mq["x"] = float(e.local_position.x)
        vy = float(e.local_position.y)
        _mq["last_vy"] = vy
        _mq["y"] = vy + _scroll[0]
        _marquee_apply()
        try:
            vh = float(page.height or page.window.height or 720) - 46 - 46    # header + top bar
        except Exception:
            vh = 600.0
        if vy < 28:
            page.run_task(_autoscroll, -40.0)
        elif vy > vh - 28:
            page.run_task(_autoscroll, 40.0)

    def _pan_end(e):
        if not _mq["on"]: return
        _mq["on"] = False
        marquee.visible = False
        _update_gallery_count()
        page.update()

    def _background_tap(e):
        """A click on empty space (not on a tile) clears the selection, like a file browser."""
        if not (_mods["ctrl"] or _mods["shift"]) and _sel:
            _clear_selection()

    gallery_area = ft.GestureDetector(
        content=ft.Stack([
            ft.Container(content=gallery_col, left=0, top=0, right=0, bottom=0),
            marquee,
        ], expand=True),
        on_pan_down=_pan_down, on_pan_start=_pan_start, on_pan_update=_pan_update, on_pan_end=_pan_end,
        on_tap=_background_tap, drag_interval=30, expand=True,
    )

    center_panel = ft.Container(
        content=ft.Column([
            gallery_top_bar,
            ft.Container(content=gallery_area, expand=True),
        ], spacing=0, expand=True),
        expand=True,
    )

    # ── Keyboard ──────────────────────────────────────────────────────
    def _set_mod(key: str, down: bool):
        k = (key or "").lower()
        if "shift" in k: _mods["shift"] = down
        elif "control" in k or "ctrl" in k or "meta" in k or "super" in k or "command" in k:
            _mods["ctrl"] = down

    def _on_key_down(e):
        _set_mod(getattr(e, "key", ""), True)

    def _on_key_up(e):
        _set_mod(getattr(e, "key", ""), False)

    def _on_keyboard(e: ft.KeyboardEvent):
        # every key-down carries the live modifier flags: use them to heal a missed key-up
        _mods["ctrl"] = bool(e.ctrl or e.meta); _mods["shift"] = bool(e.shift)
        if _confirm_open[0]: return
        k = e.key
        if lightbox_dlg.open:
            if k == "Arrow Left":    nav_prev()
            elif k == "Arrow Right": nav_next()
            elif k == "Escape":      close_lightbox()
            elif k == "Delete":      delete_current()
            return
        if _typing: return                  # text fields keep Ctrl+A / Delete / Esc for themselves
        if (e.ctrl or e.meta) and k.lower() == "a":
            _select_all()
        elif k == "Delete":
            _delete_selected()
        elif k == "Escape":
            _clear_selection()

    page.on_keyboard_event = _on_keyboard

    # ══════════════════════════════════════════════════════════════════
    # RIGHT — Settings + Run
    # ══════════════════════════════════════════════════════════════════

    def section(title_str, *children, icon=None):
        hdr = []
        if icon: hdr.append(ft.Icon(icon, size=13, color=C["text3"]))
        hdr.append(ft.Text(title_str, size=11, weight=ft.FontWeight.W_600, color=C["text2"]))
        return ft.Container(
            content=ft.Column([ft.Row(hdr, spacing=5), *children], spacing=6),
            padding=po(left=14, right=14, top=12, bottom=12),
            border=ft.Border(bottom=ft.BorderSide(1, C["border"])),
        )

    # ── Selected APIs ────────────────────────────────────────────────────
    def _entry_alias(en: dict) -> str:
        return (en.get("alias") or "").strip() or Path(en["file"]).stem

    def _api_status(file: str) -> tuple[str, str]:
        """(short status text, level) — level is 'ok', 'warn' or 'bad'."""
        info = _api_info(file)
        if not info["exists"]: return "Missing workflow", "bad"
        if info["error"]: return "Can't read workflow", "bad"
        parts, level = [], "ok"
        if info["prompt"]: parts.append("Prompt ✓")
        else: parts.append("No prompt input"); level = "bad"
        parts.append("Seed ✓" if info["seed"] else "Seed fixed")
        if info["needs_image"]:
            parts.append("Requires image")
            if level == "ok": level = "warn"
        return " · ".join(parts), level

    _LEVEL_COLOR = {"ok": C["text3"], "warn": C["amber"], "bad": C["red"]}

    def _apis_problems() -> list[str]:
        """Reasons the current API list cannot generate right now (empty list = fine)."""
        out = []
        if not selected_apis:
            return ["Add at least one API before generating."]
        miss = [_entry_alias(a) for a in selected_apis if not _api_info(a["file"])["exists"]]
        if miss:
            out.append("Missing workflow: " + ", ".join(miss) + ". Remove or replace it first.")
        if not SOURCE_IMAGE_ENABLED:
            req = [_entry_alias(a) for a in selected_apis if _api_info(a["file"])["needs_image"]]
            if req:
                out.append("One or more selected workflows require a source image. "
                           "Source Image mode is currently unavailable: " + ", ".join(req))
        return out

    apis_col = ft.Column(spacing=4)
    apis_warn = ft.Text("", size=9, color=C["red"], italic=True)

    def _rebuild_apis_list():
        apis_col.controls.clear()
        if not selected_apis:
            apis_col.controls.append(ft.Text("No APIs selected", size=10, color=C["text3"], italic=True))
        for i, en in enumerate(selected_apis):
            status, level = _api_status(en["file"])
            apis_col.controls.append(ft.Container(
                content=ft.Row([
                    ft.Container(content=ft.Text(str(i + 1), size=9, color=C["accent"], weight=ft.FontWeight.W_700),
                                 width=18, height=18, bgcolor=C["accent_lt"], border_radius=br(9),
                                 alignment=ft.Alignment(0, 0)),
                    ft.Column([
                        ft.Text(_entry_alias(en), size=11, color=C["text1"], weight=ft.FontWeight.W_600,
                                max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(f"{en['file']} · {status}" if level != "bad" or "Missing" not in status
                                else f"⚠ {status} · {en['file']}",
                                size=9, color=_LEVEL_COLOR[level], max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS),
                    ], spacing=0, expand=True),
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor=C["panel"], border_radius=br(6), tooltip=f"{_entry_alias(en)}\n{en['file']}\n{status}",
                padding=po(left=6, right=6, top=4, bottom=4),
                border=ft.Border.all(1, C["red"] if level == "bad" else C["border"]),
            ))
        probs = _apis_problems() if selected_apis else []
        apis_warn.value = "\n".join(probs)
        apis_warn.visible = bool(probs)
        try: page.update()
        except Exception: pass

    # ── Image input section (shown/hidden based on workflow) ────────────────────
    img_preview = ft.Image(src="", fit=ft.BoxFit.CONTAIN, width=220, height=120, visible=False)
    img_placeholder = ft.Column([
        ft.Icon(ft.Icons.ADD_PHOTO_ALTERNATE_OUTLINED, size=32, color=C["text3"]),
        ft.Text("Select source image", size=11, color=C["text3"]),
    ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=6)
    img_box = ft.Container(
        content=ft.Stack([img_placeholder, img_preview]),
        bgcolor=C["panel"], border_radius=br(8),
        border=ft.Border(top=ft.BorderSide(1, C["border"]), right=ft.BorderSide(1, C["border"]),
                         bottom=ft.BorderSide(1, C["border"]), left=ft.BorderSide(1, C["border"])),
        height=130, alignment=ft.Alignment(0, 0), clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )

    async def pick_image(e):
        fp = ft.FilePicker()
        files = await fp.pick_files(dialog_title="Select source image", allow_multiple=False,
                                    allowed_extensions=["jpg", "jpeg", "png", "webp", "bmp"])
        if files and files[0].path:
            p = files[0].path
            input_image.clear(); input_image.append(p)
            img_preview.src = _to_src(p)
            img_preview.visible = True
            img_placeholder.visible = False
            _save_state()
            page.update()

    img_pick_btn = ft.FilledButton(
        content=ft.Row([ft.Icon(ft.Icons.PHOTO_LIBRARY_OUTLINED, size=14, color="#FFF"),
                        ft.Text("Select image", size=11, color="#FFF")], tight=True, spacing=5),
        on_click=pick_image, bgcolor=C["text2"], height=30,
    )

    image_section_container = ft.Container(
        content=ft.Column([
            ft.Row([ft.Icon(ft.Icons.IMAGE_OUTLINED, size=13, color=C["text3"]),
                   ft.Text("Source image", size=11, weight=ft.FontWeight.W_600, color=C["text2"])], spacing=5),
            img_box,
            ft.Container(content=img_pick_btn, alignment=ft.Alignment(1, 0)),
        ], spacing=6),
        padding=po(left=14, right=14, top=12, bottom=12),
        border=ft.Border(bottom=ft.BorderSide(1, C["border"])),
        visible=True,
    )

    image_section_container.visible = False        # Source Image is hidden in this version (see SOURCE_IMAGE_ENABLED)

    # ── Working Folder ───────────────────────────────────────────────
    folder_lbl = ft.Text(
        Path(working_dir[0]).name or working_dir[0] if working_dir[0] else "Not selected yet",
        size=10, color=C["accent"] if working_dir[0] else C["text3"],
        overflow=ft.TextOverflow.ELLIPSIS, expand=True, max_lines=1,
        tooltip=working_dir[0] or "Choose the folder used for images and gallery data",
    )

    def _open_working_folder(path: str) -> bool:
        """Make `path` the Working Folder: load its gallery data, reconcile it with the files on disk,
        show the result and keep generating into it. Selection, viewer, marquee, scroll and caches belong
        to the previous folder and are reset."""
        if _is_running[0]:
            status_lbl.value = "⚠ Stop generating before changing the Working Folder"; page.update(); return False
        folder = Path(path)
        if not folder.is_dir():
            status_lbl.value = f"⚠ Not a folder: {path}"; page.update(); return False
        if working_dir[0]:
            _save_state()                                  # leave the old folder's data complete
        lightbox_dlg.open = False
        _sel.clear(); _sel_anchor[0] = None; _sel_focus[0] = None
        _mq["on"] = False; marquee.visible = False
        _hovered.clear(); _last_down[0], _last_down[1] = None, 0.0
        _SRC_CACHE.clear(); _THUMB_CACHE.clear(); _dims_cache.clear()
        st = _read_folder_state(folder)
        if not st["had_meta"] and not st["corrupt"] and not working_dir[0] and _draft[0]:
            d = _clean_settings(_draft[0])                 # first folder ever: adopt the setup typed so far
            st.update({k: d[k] for k in ("prompts", "selected_apis", "rounds_count", "sel_size", "wildcard_text")})
        _draft[0] = None
        prompts_list[:] = st["prompts"]
        selected_apis[:] = [dict(x) for x in st["selected_apis"]]
        rounds_count[0] = st["rounds_count"]
        _sel_size[0] = st["sel_size"] if st["sel_size"] in _size_map else _DEFAULT_SIZE
        wildcard_text[0] = st["wildcard_text"]
        gallery_by_prompt.clear()
        for it in st["items"]:
            gallery_by_prompt.setdefault(it.get("prompt", ""), []).append(it)
        working_dir[0] = str(folder)
        folder_lbl.value = folder.name or str(folder)
        folder_lbl.tooltip = str(folder)
        folder_lbl.color = C["accent"]
        rounds_field.value = str(rounds_count[0])
        size_dd.value = _sel_size[0]
        wildcard_field.value = wildcard_text[0]
        wildcard_preview_lbl.value = ""
        _rebuild_prompt_list(); _rebuild_apis_list()
        _scroll[0] = 0.0
        rebuild_gallery()
        page.run_task(_scroll_to, 0.0)
        _save_state()
        note = f"{_pl(len(st['items']), 'image')}"
        if st["imported"]: note += f", {st['imported']} in Other Images"
        if st["pruned"]: note += f", {_pl(st['pruned'], 'missing file')} dropped"
        if st["corrupt"]: note += " — gallery data was unreadable, rebuilt from the files"
        status_lbl.value = f"📁 {folder.name or folder}: {note}"
        page.update()
        return True

    async def pick_folder(e):
        if _is_running[0]:
            status_lbl.value = "⚠ Stop generating before changing the Working Folder"; page.update(); return
        fp = ft.FilePicker()
        result = await fp.get_directory_path("Select Working Folder")
        if result:
            _open_working_folder(result)

    folder_btn = ft.IconButton(icon=ft.Icons.FOLDER_OPEN_OUTLINED, icon_color=C["text2"],
                               icon_size=18, on_click=pick_folder, width=30, height=30,
                               padding=0, tooltip="Choose the folder used for images and gallery data")

    # ── Prompt list ──────────────────────────────────────────────────
    prompt_list_col = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, height=160)

    prompt_input_field = ft.TextField(
        hint_text="Type a prompt and press Enter to add",
        text_size=11, border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=8, right=8, top=6, bottom=6),
        height=38,
    )

    def _rebuild_prompt_list():
        prompt_list_col.controls.clear()
        if not prompts_list:
            prompt_list_col.controls.append(
                ft.Text("No prompts yet", size=10, color=C["text3"], italic=True)
            )
        else:
            for i, p in enumerate(prompts_list):
                def _del(e, _i=i):
                    prompts_list.pop(_i)
                    _save_state(); _rebuild_prompt_list(); page.update()
                prompt_list_col.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Container(
                                content=ft.Text(str(i+1), size=9, color=C["accent"], weight=ft.FontWeight.W_700),
                                width=18, height=18, bgcolor=C["accent_lt"],
                                border_radius=br(9), alignment=ft.Alignment(0, 0),
                            ),
                            ft.Text(p, size=10, color=C["text1"], expand=True,
                                   max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                            ft.IconButton(icon=ft.Icons.CLOSE_ROUNDED, icon_size=12,
                                         icon_color=C["text3"], on_click=_del,
                                         width=22, height=22, padding=0),
                        ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        bgcolor=C["panel"], border_radius=br(5),
                        padding=po(left=6, right=2, top=3, bottom=3),
                    )
                )

    _rebuild_prompt_list()

    def add_prompt(e):
        txt = (prompt_input_field.value or "").strip()
        if txt:
            prompts_list.append(txt)
            prompt_input_field.value = ""
            _save_state(); _rebuild_prompt_list()
            page.update()

    prompt_input_field.on_submit = add_prompt

    add_prompt_btn = ft.IconButton(
        icon=ft.Icons.ADD_CIRCLE_ROUNDED, icon_color=C["accent"], icon_size=22,
        on_click=add_prompt, width=34, height=34, padding=0,
    )

    # ── Rounds per prompt ────────────────────────────────────────────
    rounds_field = ft.TextField(
        value=str(rounds_count[0]),
        hint_text="Rounds per prompt",
        text_size=11, border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=8, right=8, top=6, bottom=6),
        width=90, height=36, keyboard_type=ft.KeyboardType.NUMBER,
    )

    def rounds_changed(e):
        try:
            rounds_count[0] = max(1, int(rounds_field.value or "1"))
        except ValueError:
            rounds_count[0] = 1
        _save_state()

    rounds_field.on_change = rounds_changed

    # ── Size selector (similar to prompt tester) ─────────────────────────
    _ALL_SIZES = [
        ("[1:1] 512 × 512",    512,  512),  ("[1:1] 768 × 768",    768,  768),
        ("[1:1] 1024 × 1024", 1024, 1024),  ("[1:1] 2048 × 2048", 2048, 2048),
        ("[3:2] 576 × 384",    576,  384),  ("[3:2] 768 × 512",    768,  512),
        ("[3:2] 1536 × 1024", 1536, 1024),  ("[3:2] 2304 × 1536", 2304, 1536),
        ("[4:3] 512 × 384",    512,  384),  ("[4:3] 768 × 576",    768,  576),
        ("[4:3] 1024 × 768",  1024,  768),  ("[4:3] 2048 × 1536", 2048, 1536),
        ("[16:9] 768 × 432",   768,  432),  ("[16:9] 1280 × 720", 1280,  720),
        ("[16:9] 1920 × 1080",1920, 1080),  ("[16:9] 2304 × 1296",2304, 1296),
        ("[2:3] 384 × 576",    384,  576),  ("[2:3] 512 × 768",    512,  768),
        ("[2:3] 1024 × 1536", 1024, 1536),  ("[2:3] 1536 × 2304", 1536, 2304),
        ("[3:4] 384 × 512",    384,  512),  ("[3:4] 576 × 768",    576,  768),
        ("[3:4] 768 × 1024",   768, 1024),  ("[3:4] 1536 × 2048", 1536, 2048),
        ("[9:16] 432 × 768",   432,  768),  ("[9:16] 720 × 1280",  720, 1280),
        ("[9:16] 1080 × 1920",1080, 1920),  ("[9:16] 1296 × 2304",1296, 2304),
    ]
    _size_map = {lbl: (w, h) for lbl, w, h in _ALL_SIZES}

    size_dd = ft.Dropdown(
        value=_sel_size[0],
        options=[ft.dropdown.Option(lbl) for lbl, *_ in _ALL_SIZES],
        text_size=11, height=38, border_color=C["border"],
        focused_border_color=C["accent"], bgcolor=C["surface"],
        content_padding=po(left=8, right=4, top=0, bottom=0),
    )

    def size_changed(e):
        _sel_size[0] = size_dd.value or "[16:9] 2304 × 1296"
        _save_state()

    size_dd.on_change = size_changed

    # ── Add API dialog: AVAILABLE | SELECTED, edited on a working copy ────────────────────────────
    def _open_add_api_dialog(e=None):
        if _is_running[0]: return
        work = [dict(x) for x in selected_apis]                 # temporary copy: Cancel discards it
        files = [scan_api_files()]
        hl_left: set = set(); hl_right: set = set()              # highlighted rows (file names)
        left_list = ft.ListView(spacing=2, expand=True)
        right_list = ft.ListView(spacing=2, expand=True)
        note = ft.Text("", size=10, color=C["text3"])
        clear_btn = ft.TextButton(
            content=ft.Row([ft.Icon(ft.Icons.DESELECT_ROUNDED, size=14, color=C["text2"]),
                            ft.Text("Clear selection", size=11, color=C["text2"])], tight=True, spacing=4),
            on_click=lambda e: clear_selection(), visible=False)

        def _chosen() -> set:
            return {w["file"] for w in work}

        def _row(title, sub, level, highlighted, on_tap, tip):
            return ft.GestureDetector(
                content=ft.Container(
                    content=ft.Column([
                        ft.Text(title, size=12, color=C["text1"], weight=ft.FontWeight.W_600, max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(sub, size=10, color=_LEVEL_COLOR[level], max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS),
                    ], spacing=1),
                    padding=po(left=10, right=10, top=6, bottom=6), border_radius=br(6), tooltip=tip,
                    bgcolor=C["accent_lt"] if highlighted else C["surface"],
                    border=ft.Border.all(1, C["accent"] if highlighted else C["border"]),
                ),
                on_tap=on_tap, mouse_cursor=ft.MouseCursor.CLICK)

        def render():
            left_list.controls.clear(); right_list.controls.clear()
            chosen = _chosen()
            hl_left.intersection_update(set(files[0])); hl_right.intersection_update(chosen)
            avail = [f for f in files[0] if f not in chosen]
            for f in avail:
                status, level = _api_status(f)
                left_list.controls.append(_row(
                    Path(f).stem, f"{f} · {status}", level, f in hl_left,
                    lambda e, _f=f: toggle(hl_left, _f), f))
            if not avail:
                left_list.controls.append(ft.Text("No more APIs available", size=10, color=C["text3"], italic=True))
            for i, w in enumerate(work):
                status, level = _api_status(w["file"])
                right_list.controls.append(_row(
                    f"{i + 1}.  {_entry_alias(w)}", f"{w['file']} · {status}", level, w["file"] in hl_right,
                    lambda e, _f=w["file"]: toggle(hl_right, _f),
                    f"{_entry_alias(w)}\n{w['file']}"))
            if not work:
                right_list.controls.append(ft.Text("No APIs selected", size=10, color=C["text3"], italic=True))
            clear_btn.visible = bool(hl_left or hl_right)
            page.update()

        def toggle(group: set, f: str):
            group.symmetric_difference_update({f}); render()

        def clear_selection(e=None):
            hl_left.clear(); hl_right.clear(); render()

        def add_files(names):
            for f in names:
                if f not in _chosen():
                    work.append({"file": f, "alias": Path(f).stem})
            hl_left.clear(); render()

        def remove_files(names):
            work[:] = [w for w in work if w["file"] not in set(names)]
            hl_right.clear(); render()

        def do_add(e):
            add_files([f for f in files[0] if f in hl_left])

        def do_remove(e):
            remove_files(list(hl_right))

        def move(delta: int):
            idx = [i for i, w in enumerate(work) if w["file"] in hl_right]
            for i in (idx if delta < 0 else reversed(idx)):
                j = i + delta
                if 0 <= j < len(work) and work[j]["file"] not in hl_right:
                    work[i], work[j] = work[j], work[i]
            render()

        def do_refresh(e):
            _API_INFO_CACHE.clear()
            files[0] = scan_api_files()
            note.value = f"{_pl(len(files[0]), 'API')} found"
            render()

        def do_apply(e):
            selected_apis[:] = [dict(w) for w in work]
            page.pop_dialog()
            _save_state(); _rebuild_apis_list()

        def do_cancel(e):
            page.pop_dialog()

        def _mid_btn(icon, tip, on_click):
            return ft.IconButton(icon=icon, tooltip=tip, on_click=on_click, icon_size=18, width=36, height=36,
                                 icon_color=C["text1"], style=ft.ButtonStyle(
                                     side=ft.BorderSide(1, C["border2"]), shape=ft.RoundedRectangleBorder(radius=6)))

        try:
            pw = float(page.width or page.window.width or 1280); ph = float(page.height or page.window.height or 720)
        except Exception:
            pw, ph = 1280.0, 720.0
        dw, dh = int(max(560, min(940, pw * 0.9))), int(max(380, min(600, ph * 0.72)))

        def _panel(title, lst):
            return ft.Column([
                ft.Text(title, size=10, weight=ft.FontWeight.W_700, color=C["text3"]),
                ft.GestureDetector(
                    content=ft.Container(content=lst, expand=True, padding=6, bgcolor=C["panel"], border_radius=br(8),
                                         border=ft.Border.all(1, C["border"])),
                    on_tap=clear_selection, expand=True),
            ], spacing=4, expand=True)

        body = ft.Container(width=dw, height=dh, content=ft.Column([
            ft.Row([
                _panel("AVAILABLE APIs", left_list),
                ft.Column([
                    _mid_btn(ft.Icons.CHEVRON_RIGHT_ROUNDED, "Add selected", do_add),
                    _mid_btn(ft.Icons.CHEVRON_LEFT_ROUNDED, "Remove selected", do_remove),
                    ft.Container(height=14),
                    _mid_btn(ft.Icons.ARROW_UPWARD_ROUNDED, "Move up", lambda ev: move(-1)),
                    _mid_btn(ft.Icons.ARROW_DOWNWARD_ROUNDED, "Move down", lambda ev: move(1)),
                ], alignment=ft.MainAxisAlignment.CENTER, horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                   spacing=6),
                _panel("SELECTED APIs — generation order", right_list),
            ], expand=True, spacing=12, vertical_alignment=ft.CrossAxisAlignment.STRETCH),
            ft.Row([
                ft.TextButton(content=ft.Row([ft.Icon(ft.Icons.REFRESH_ROUNDED, size=14, color=C["text2"]),
                                              ft.Text("Refresh", size=11, color=C["text2"])], tight=True, spacing=4),
                              on_click=do_refresh),
                clear_btn,
                note,
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=8))
        render()
        page.show_dialog(ft.AlertDialog(
            modal=True, bgcolor=C["surface"],
            title=ft.Text("Add APIs", size=15, weight=ft.FontWeight.W_700, color=C["text1"]),
            content=body,
            actions=[ft.TextButton("Cancel", on_click=do_cancel, style=ft.ButtonStyle(color=C["text2"])),
                     ft.FilledButton("Apply", on_click=do_apply, bgcolor=C["accent"])],
            actions_alignment=ft.MainAxisAlignment.END))

    add_api_btn = ft.OutlinedButton(
        content=ft.Row([ft.Icon(ft.Icons.ADD_ROUNDED, size=13, color=C["accent"]),
                        ft.Text("Add API", size=10, color=C["accent"])], tight=True, spacing=4),
        on_click=_open_add_api_dialog, height=30, tooltip="Choose which APIs to generate with, and their order",
        style=ft.ButtonStyle(side=ft.BorderSide(1, C["accent"])))

    _rebuild_apis_list()

    def limit_changed(e): pass  # compat

    # ── Wildcard field ───────────────────────────────────────────────
    wildcard_field = ft.TextField(
        value=wildcard_text[0],
        multiline=True, min_lines=2, max_lines=4,
        hint_text="{red|tall},{blue|slow}",
        text_size=10, border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"], content_padding=po(left=8, right=8, top=6, bottom=6),
    )

    def wildcard_changed(e):
        wildcard_text[0] = wildcard_field.value or ""
        _save_state()

    wildcard_field.on_change = wildcard_changed

    wildcard_preview_lbl = ft.Text("", size=9, color=C["green"], italic=True,
                                   max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)

    def preview_wildcard(e):
        if wildcard_text[0].strip():
            resolved = resolve_wildcards(wildcard_text[0])
            wildcard_preview_lbl.value = f"🎲 {resolved}"
        else:
            wildcard_preview_lbl.value = ""
        page.update()

    wildcard_roll_btn = ft.IconButton(
        icon=ft.Icons.CASINO_OUTLINED, icon_color=C["amber"], icon_size=18,
        on_click=preview_wildcard, width=30, height=30, padding=0,
        tooltip="Preview randomly",
    )

    # ── Status + Run/Stop ────────────────────────────────────────────
    status_lbl = ft.Text("", size=10, color=C["text3"], italic=True,
                         text_align=ft.TextAlign.CENTER)
    gen_count_lbl = ft.Text("", size=10, color=C["accent"], weight=ft.FontWeight.W_600,
                            text_align=ft.TextAlign.CENTER)
    run_lbl  = ft.Text("▶  Start Generate", size=13, color="#FFF", weight=ft.FontWeight.W_600)
    stop_lbl = ft.Text("⏹  Stop", size=13, color="#FFF", weight=ft.FontWeight.W_600)

    run_btn  = ft.FilledButton(content=run_lbl, bgcolor=C["accent"], width=228, height=44)
    stop_btn = ft.FilledButton(content=stop_lbl, bgcolor=C["red"], width=228, height=44, visible=False)

    def _set_running(running: bool):
        run_btn.visible  = not running
        stop_btn.visible = running
        folder_btn.disabled = running                       # one run must not write across two folders
        add_api_btn.disabled = running
        _is_running[0] = running
        page.update()

    async def _on_stop(e):
        _stop_event.set()
        status_lbl.value = "⏹ Stopping..."
        stop_btn.disabled = True
        page.update()
        pid = _ACTIVE_PID[0]
        if pid:
            await asyncio.get_event_loop().run_in_executor(None, comfy_cancel, pid)

    stop_btn.on_click = _on_stop

    async def run_loop(e):
        if not prompts_list:
            status_lbl.value = "⚠ No prompt yet"; page.update(); return
        if not working_dir[0]:
            status_lbl.value = "⚠ Working Folder not selected"; page.update(); return

        # Sync values from the dropdown directly before running (safety net)
        if size_dd.value:
            _sel_size[0] = size_dd.value

        # The selected API list, in order, IS the run list. Anything that would change the intended output
        # (empty list, a missing workflow file, a workflow that needs the hidden source image) blocks the run.
        _problems = _apis_problems()
        if _problems:
            status_lbl.value = "⚠ " + _problems[0]; page.update(); return
        _apis = [a["file"] for a in selected_apis]
        _alias = {a["file"]: _entry_alias(a) for a in selected_apis}

        _stop_event.clear()
        _set_running(True)
        stop_btn.disabled = False

        rounds = rounds_count[0]
        total = len(prompts_list) * len(_apis) * rounds
        n = 0

        # Source Image is hidden: no workflow here needs one (checked above) and no saved image is ever sent.
        _api_needs_img = {_api: _api_info(_api)["needs_image"] for _api in _apis}

        for base_prompt in prompts_list:
            if _stop_event.is_set(): break
            for cur_api in _apis:
                if _stop_event.is_set(): break
                for r in range(rounds):
                    if _stop_event.is_set(): break
                    n += 1

                    # resolve wildcards
                    resolved = resolve_wildcards(base_prompt)
                    wc = wildcard_text[0].strip()
                    if wc:
                        resolved_wc = resolve_wildcards(wc)
                        prompt_text = f"{resolved}, {resolved_wc}" if resolved_wc else resolved
                    else:
                        prompt_text = resolved

                    status_lbl.value = f"⏳ {n}/{total} — prompt {prompts_list.index(base_prompt)+1} [{_alias[cur_api]}] round {r+1}/{rounds}"
                    gen_count_lbl.value = prompt_text[:50] + ("…" if len(prompt_text) > 50 else "")
                    page.update()

                    def _prog(txt, _api=cur_api):
                        gen_count_lbl.value = f"{txt} [{_alias.get(_api, _api)}]"
                        page.update()

                    try:
                        _w, _h = _size_map.get(_sel_size[0], (2304, 1296))
                        out_path = await run_one(
                            prompt_text, Path(working_dir[0]), cur_api,
                            image_path=(input_image[0] if (SOURCE_IMAGE_ENABLED and _api_needs_img.get(cur_api)
                                                           and input_image) else None),   # never a stale saved image
                            on_progress=_prog, index=n,
                            width=_w, height=_h,
                            stop_event=_stop_event,
                        )
                        if out_path:
                            new_item = {
                                "path": out_path, "file": Path(out_path).name, "source": "generated",
                                "prompt": base_prompt, "ts": time.time(), "api": cur_api,
                            }
                            gallery_add_one(base_prompt, new_item)      # mutate state + UI first ...
                            _save_state()                               # ... then persist, so the file never lags
                    except Exception as ex:
                        status_lbl.value = f"⚠ #{n} error: {ex}"
                        page.update()

        stopped = _stop_event.is_set()
        status_lbl.value = f"⏹ Stopped — generated {n} image(s)" if stopped else f"✓ Completed — generated {n} image(s)"
        gen_count_lbl.value = ""
        _set_running(False)
        page.update()

    run_btn.on_click = run_loop

    def group_header(title: str) -> ft.Container:
        """Group heading — darker background than a normal section."""
        return ft.Container(
            content=ft.Text(title, size=10, weight=ft.FontWeight.W_700, color=C["text3"]),
            padding=po(left=14, top=10, right=14, bottom=6),
            bgcolor=C["border"],
        )

    right_panel = ft.Container(
        content=ft.Column([
            # ── Header ──────────────────────────────────────────────
            ft.Container(
                content=ft.Text("Settings", size=13,
                                weight=ft.FontWeight.W_700, color=C["text1"]),
                padding=po(left=14, top=14, right=14, bottom=10),
            ),
            divider(),

            ft.Column([
                # ══ GROUP 1: FILES ═════════════════════════════════
                group_header("FILES"),
                section("Working Folder", icon=ft.Icons.FOLDER_OUTLINED,
                        *[ft.Row([folder_lbl, folder_btn], spacing=4,
                                 vertical_alignment=ft.CrossAxisAlignment.CENTER)]),

                # ══ GROUP 2: WORKFLOW ══════════════════════════════
                group_header("WORKFLOW"),
                section("Selected APIs", icon=ft.Icons.ACCOUNT_TREE_OUTLINED,
                        *[apis_col, apis_warn, ft.Container(content=add_api_btn, alignment=ft.Alignment(-1, 0))]),

                # ══ GROUP 3: PROMPT ════════════════════════════════
                group_header("PROMPT"),
                section("Prompt List", icon=ft.Icons.FORMAT_LIST_NUMBERED_ROUNDED,
                        *[ft.Row([prompt_input_field, add_prompt_btn], spacing=4,
                                 vertical_alignment=ft.CrossAxisAlignment.CENTER),
                          prompt_list_col,
                          ft.Text("Use {a|b|c} in prompts to randomize values",
                                  size=9, color=C["text3"], italic=True),
                        ]),
                section("Additional wildcard", icon=ft.Icons.CASINO_OUTLINED,
                        *[ft.Row([
                              ft.Container(content=wildcard_field, expand=True),
                              wildcard_roll_btn,
                          ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.START),
                          wildcard_preview_lbl,
                          ft.Text("Append to every prompt; use {a|b|c} to randomize",
                                  size=9, color=C["text3"], italic=True),
                        ]),

                # ══ GROUP 4: GENERATION ════════════════════════════
                group_header("GENERATION"),
                section("Image size", icon=ft.Icons.ASPECT_RATIO_ROUNDED,
                        *[size_dd]),
                section("Number of rounds", icon=ft.Icons.NUMBERS_ROUNDED,
                        *[ft.Row([
                              rounds_field,
                              ft.Text("rounds / prompt", size=10, color=C["text3"]),
                          ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                          ft.Text(
                              lambda: f"Total {len(prompts_list) * rounds_count[0]} image(s)" if prompts_list else "Add prompts first",
                              size=9, color=C["accent"], italic=True,
                          ) if False else
                          ft.Text("", size=9, color=C["accent"], italic=True),
                        ]),

            ], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True),

            divider(),
            # ── Run / Stop ───────────────────────────────────────
            ft.Container(
                content=ft.Column([
                    status_lbl,
                    gen_count_lbl,
                    ft.Container(content=run_btn, alignment=ft.Alignment(0, 0)),
                    ft.Container(content=stop_btn, alignment=ft.Alignment(0, 0)),
                ], spacing=8, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                padding=po(left=14, right=14, top=12, bottom=14),
            ),
        ], spacing=0, expand=True),
        width=290,
        bgcolor=C["panel"],
        border=ft.Border(left=ft.BorderSide(1, C["border2"])),
    )

    # ── Init image preview if saved ───────────────────────────────────
    if input_image and Path(input_image[0]).exists():
        img_preview.src = _to_src(input_image[0])
        img_preview.visible = True
        img_placeholder.visible = False

    _update_gallery_count()
    rebuild_gallery()
    # Load the old gallery (backward compat: old format was a flat list)
    # gallery_by_prompt is populated during state initialization

    # Gallery shortcuts (Ctrl+A / Delete / Esc) must never act while the user is typing in a field.
    def _field_focus(field, on: bool):
        (_typing.add if on else _typing.discard)(id(field))
    for _f in (prompt_input_field, rounds_field, wildcard_field):
        _f.on_focus = lambda e, _f=_f: _field_focus(_f, True)
        _f.on_blur = lambda e, _f=_f: (_field_focus(_f, False), _mods.update(ctrl=False, shift=False))

    # A zero-size KeyboardListener is the focus anchor: it tracks Ctrl/Shift held state for click-selection
    # (Flet's page keyboard event only reports key-down) and lets a thumbnail click take keyboard focus
    # off a text field. It is NOT wrapped around the layout — a wrapper broke the window's height layout.
    _kb_listener[0] = ft.KeyboardListener(
        content=ft.Container(width=1, height=1),
        on_key_down=_on_key_down, on_key_up=_on_key_up, autofocus=True,
    )
    header.content.controls.append(_kb_listener[0])

    async def _no_browser_menu():              # web only: keep the browser's own right-click menu out of the way
        try: await page.browser_context_menu.disable()
        except Exception: pass
    page.run_task(_no_browser_menu)

    page.add(ft.Column([
        header,
        ft.Row([center_panel, right_panel], spacing=0, expand=True),
    ], spacing=0, expand=True))


ft.app(target=main)