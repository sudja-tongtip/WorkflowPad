"""
comfy_api.py — ComfyUI HTTP helpers + workflow build/run
"""
__version__ = "20260726_1907"

import asyncio, json, random, time, urllib.parse, urllib.request, urllib.error, uuid
from pathlib import Path

from .constants import COMFY_HOST as _DEFAULT_COMFY_HOST, API_DIR, clean_overlay_text

_comfy_host = _DEFAULT_COMFY_HOST

def set_comfy_host(host: str):
    global _comfy_host
    _comfy_host = host.strip() or _DEFAULT_COMFY_HOST

# ── ComfyUI HTTP ──────────────────────────────────────────────────────
def comfy_upload_image(path: str) -> str:
    with open(path, "rb") as f: data = f.read()
    filename = Path(path).name
    boundary = "----B" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
        f"filename=\"{filename}\"\r\nContent-Type: image/jpeg\r\n\r\n"
    ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"http://{_comfy_host}/upload/image", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())["name"]

def comfy_queue(wf: dict, client_id: str) -> str:
    payload = json.dumps({"prompt": wf, "client_id": client_id}).encode()
    req = urllib.request.Request(
        f"http://{_comfy_host}/prompt", data=payload,
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["prompt_id"]
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        raise RuntimeError(f"ComfyUI rejected the workflow — HTTP {e.code}: {body[:800]}") from None

def comfy_history(pid: str) -> dict:
    with urllib.request.urlopen(f"http://{_comfy_host}/history/{pid}", timeout=10) as r:
        return json.loads(r.read())

def comfy_delete_history(pid: str) -> None:
    """Remove this prompt from ComfyUI history to prevent history bloat"""
    try:
        payload = json.dumps({"delete": [pid]}).encode()
        req = urllib.request.Request(
            f"http://{_comfy_host}/history", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

def comfy_cancel(pid: str) -> None:
    """Stop the task ComfyUI is running and remove it from the queue"""
    try:
        # Remove it from the queue if it has not executed yet
        payload = json.dumps({"delete": [pid]}).encode()
        req = urllib.request.Request(
            f"http://{_comfy_host}/queue", data=payload,
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass
    try:
        # Interrupt the task that is currently executing
        req = urllib.request.Request(
            f"http://{_comfy_host}/interrupt", data=b"",
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass

def comfy_get_image(filename, subfolder, folder_type) -> bytes:
    p = urllib.parse.urlencode({"filename": filename, "subfolder": subfolder, "type": folder_type})
    with urllib.request.urlopen(f"http://{_comfy_host}/view?{p}", timeout=30) as r:
        return r.read()

# ── Workflow detection ────────────────────────────────────────────────
_IMG_CLASSES    = ["LoadImage", "ETN_LoadImageBase64", "LoadImageMask",
                   "ImageBatch", "LoadImageFromUrl"]
_PROMPT_CLASSES = ["CLIPTextEncodeFlux", "CLIPTextEncodeSD3", "CLIPTextEncodeHunyuan",
                   "smZ CLIPTextEncode", "BNK_CLIPTextEncodeAdvanced", "CLIPTextEncode"]
_SEED_CLASSES   = ["RandomNoise", "KSamplerAdvanced", "KSampler",
                   "SamplerCustomAdvanced", "SamplerCustom"]
_SEED_FIELDS    = ["noise_seed", "seed", "rand_seed"]
_POS_HINTS      = {"positive", "pos", "prompt", "clip positive", "positive prompt"}
_LATENT_CLS     = ["EmptyLatentImage", "EmptySD3LatentImage", "EmptyHunyuanLatentVideo",
                   "EmptyMochiLatentVideo", "EmptyLTXVLatentVideo", "EmptyLatentImagePresets"]
_STEPS_CLASSES  = ["KSampler", "KSamplerAdvanced", "SamplerCustomAdvanced",
                   "SamplerCustom", "KSamplerSelect"]
_STEPS_FIELDS   = ["steps", "num_steps"]

def _title(nid, wf):
    return (wf.get(nid, {}).get("_meta", {}).get("title", "") or "").lower()

def detect_mapping(wf: dict) -> dict:
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
            role = "positive" if any(h in t for h in _POS_HINTS) else ("negative" if "neg" in t else "unknown")
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

def inject_size(wf: dict, width: int, height: int) -> dict:
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        if node.get("class_type", "") in _LATENT_CLS:
            inp = node.get("inputs", {})
            if "width"  in inp: wf[nid]["inputs"]["width"]  = width
            if "height" in inp: wf[nid]["inputs"]["height"] = height
    return wf

def inject_steps(wf: dict, steps: int) -> dict:
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        if node.get("class_type", "") in _STEPS_CLASSES:
            inp = node.get("inputs", {})
            for sf in _STEPS_FIELDS:
                if sf in inp:
                    wf[nid]["inputs"][sf] = steps
                    break
    return wf

ANATOMY_GUARD = (
    "Natural, realistic human anatomy with physically plausible body proportions, joints, and poses. "
    "All visible limbs must connect correctly to the body, including natural overlaps and bends. "
    "No extra or duplicated limbs, joints, or distorted body parts. "
    "Natural lower-body anatomy with the correct number of legs and feet, realistic leg placement, clear ankle structure, "
    "no buried feet, no fused limbs, and realistic weight-bearing contact between the feet and the visible surface."
)


def _with_anatomy_guard(prompt_text: str) -> str:
    """Appended once at the very end of the prompt that is sent to Krea2. Added only here, at send time, so it never
    appears in a shot, the displayed/logged final prompt, or the gallery trace."""
    if ANATOMY_GUARD in (prompt_text or ""):
        return prompt_text
    return f"{prompt_text}\n\n{ANATOMY_GUARD}" if (prompt_text or "").strip() else prompt_text


def build_workflow(api_filename: str, prompt_text: str,
                   width: int = 0, height: int = 0,
                   fixed_seed: int = -1,
                   override_steps: int = -1) -> dict:
    prompt_text = _with_anatomy_guard(prompt_text)
    with open(API_DIR / api_filename, encoding="utf-8") as f: wf = json.load(f)
    m = detect_mapping(wf)
    for ph in m["prompt_nodes"]:
        if ph["role"] in ("positive", "unknown"):
            wf[ph["node"]]["inputs"][ph["field"]] = prompt_text; break
    if m["seed_node"]:
        seed = fixed_seed if fixed_seed >= 0 else random.randint(0, 2**53)
        wf[m["seed_node"]]["inputs"][m["seed_field"]] = seed
    if width > 0 and height > 0: wf = inject_size(wf, width, height)
    if override_steps > 0:       wf = inject_steps(wf, override_steps)
    return wf

async def run_one(prompt_text: str, out_dir: Path, api_filename: str,
                  width: int = 0, height: int = 0,
                  on_progress=None, index: int = 0,
                  fixed_seed: int = -1, override_steps: int = -1,
                  stop_event: asyncio.Event | None = None) -> tuple[str, int] | tuple[None, int]:
    loop = asyncio.get_event_loop()
    wf = build_workflow(api_filename, prompt_text, width, height,
                        fixed_seed=fixed_seed, override_steps=override_steps)
    m = detect_mapping(wf)
    actual_seed = wf[m["seed_node"]]["inputs"].get(m["seed_field"], -1) if m["seed_node"] else -1
    try:
        pid = await loop.run_in_executor(None, comfy_queue, wf, uuid.uuid4().hex)
    except Exception as e:
        print(f"[run_one] comfy_queue failed: {e}")
        return None, actual_seed
    tick = 0; deadline = time.time() + 300
    while time.time() < deadline:
        if stop_event and stop_event.is_set():
            await loop.run_in_executor(None, comfy_cancel, pid)
            return None, actual_seed
        await asyncio.sleep(2); tick += 1
        if on_progress: on_progress(f"🔄 {tick*2}s")
        hist = await loop.run_in_executor(None, comfy_history, pid)
        if pid not in hist: continue
        for out in hist[pid].get("outputs", {}).values():
            if "images" in out:
                info = out["images"][0]
                img  = await loop.run_in_executor(None, comfy_get_image,
                       info["filename"], info.get("subfolder", ""), info.get("type", "output"))
                out_dir.mkdir(parents=True, exist_ok=True)
                dst = out_dir / f"story_{index:02d}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}.jpg"
                dst.write_bytes(img)
                return str(dst), actual_seed
        if hist[pid].get("status", {}).get("status_str", "") in ("success", "error"): break
    return None, actual_seed

def pick_reference_image(gallery_images: dict, shot_idx: int, ref_source: str) -> str | None:
    """Find a reference image for Inpaint Mode based on ref_source:
    - "shot1": always use the latest image from shot 1 (the anchor remains fixed throughout the story)
    - "prev_shot": use the latest image from the previous shot; if unavailable (or shot_idx==0, meaning shot 1 itself), fall back to the latest image from shot 1 if present (for example, regenerating shot 1 again uses its own previous image as reference)
    Return None if there is no image to use — the caller must fall back to text-to-image"""
    def _latest(idx):
        imgs = gallery_images.get(idx, [])
        if not imgs: return None
        return sorted(imgs, key=lambda x: x.get("ts", 0), reverse=True)[0]["path"]

    if ref_source == "shot1":
        return _latest(0)
    # "prev_shot" (default): shot ก่อนหน้า, fallback shot 1
    if shot_idx > 0:
        prev = _latest(shot_idx - 1)
        if prev: return prev
    return _latest(0)

async def run_img2img(prompt_text: str, ref_image: str, out_dir: Path, api_filename: str,
                       width: int = 0, height: int = 0,
                       on_progress=None, index: int = 0,
                       fixed_seed: int = -1, override_steps: int = -1,
                       stop_event: asyncio.Event | None = None) -> tuple[str, int] | tuple[None, int]:
    """Like run_one, but injects a reference image into the workflow (for Inpaint Mode —
    uses an img2img/inpaint workflow instead of plain text-to-image, with the previous image as the reference)"""
    loop = asyncio.get_event_loop()
    _ref_instruction = "Reference the character and environment from the image exactly."
    if _ref_instruction not in prompt_text:
        prompt_text = f"{_ref_instruction}\n{prompt_text}"
    wf = build_workflow(api_filename, prompt_text, width, height,
                        fixed_seed=fixed_seed, override_steps=override_steps)
    m = detect_mapping(wf)
    actual_seed = wf[m["seed_node"]]["inputs"].get(m["seed_field"], -1) if m["seed_node"] else -1
    if m["image_node"]:
        try:
            img_name = await loop.run_in_executor(None, comfy_upload_image, ref_image)
            wf[m["image_node"]]["inputs"][m["image_field"]] = img_name
        except Exception as e:
            print(f"[run_img2img] upload error: {e}")
            return None, actual_seed
    else:
        print(f"[run_img2img] workflow {api_filename} has no image input node — running as text-to-image")
    try:
        pid = await loop.run_in_executor(None, comfy_queue, wf, uuid.uuid4().hex)
    except Exception as e:
        print(f"[run_img2img] comfy_queue failed: {e}")
        return None, actual_seed
    tick = 0; deadline = time.time() + 300
    while time.time() < deadline:
        if stop_event and stop_event.is_set():
            await loop.run_in_executor(None, comfy_cancel, pid)
            return None, actual_seed
        await asyncio.sleep(2); tick += 1
        if on_progress: on_progress(f"🔄 {tick*2}s")
        hist = await loop.run_in_executor(None, comfy_history, pid)
        if pid not in hist: continue
        for out in hist[pid].get("outputs", {}).values():
            if "images" in out:
                info = out["images"][0]
                img  = await loop.run_in_executor(None, comfy_get_image,
                       info["filename"], info.get("subfolder", ""), info.get("type", "output"))
                out_dir.mkdir(parents=True, exist_ok=True)
                dst = out_dir / f"story_{index:02d}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}.jpg"
                dst.write_bytes(img)
                return str(dst), actual_seed
        if hist[pid].get("status", {}).get("status_str", "") in ("success", "error"): break
    return None, actual_seed

async def run_joycaption(image_path: str, wf_file: str,
                          stop_event: asyncio.Event | None = None) -> str | None:
    """Send an image to JoyCaption and receive the caption back"""
    import copy
    loop = asyncio.get_event_loop()
    from .constants import CAPTION_DIR
    wf_path = CAPTION_DIR / wf_file
    if not wf_path.exists():
        return None

    with open(wf_path, encoding="utf-8") as f:
        wf = json.load(f)

    # Upload the image to ComfyUI and set the LoadImage node
    try:
        uploaded_name = await loop.run_in_executor(None, comfy_upload_image, image_path)
    except Exception as e:
        print(f"[joycaption] upload error: {e}"); return None

    # Inject the image into the LoadImage node and reset the session every time
    _session_nodes_found = []
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        ct = node.get("class_type", "")
        if ct == "LoadImage":
            wf[nid]["inputs"]["image"] = uploaded_name
        if ct == "LLMSessionChatNode":
            wf[nid]["inputs"]["session_id"]    = uuid.uuid4().hex[:8]
            wf[nid]["inputs"]["reset_session"] = True
            _session_nodes_found.append(nid)
        # Also support other node names that have a session reset field
        elif "session" in ct.lower() or "chat" in ct.lower():
            inp = node.get("inputs", {})
            if "session_id" in inp:
                wf[nid]["inputs"]["session_id"] = uuid.uuid4().hex[:8]
            if "reset_session" in inp:
                wf[nid]["inputs"]["reset_session"] = True
            _session_nodes_found.append(nid)
    if not _session_nodes_found:
        print(f"[joycaption] WARNING: no session reset node found — LLM context may accumulate")

    client_id = uuid.uuid4().hex
    try:
        pid = await loop.run_in_executor(None, comfy_queue, wf, client_id)
        print(f"[joycaption] queued pid={pid[:8]} | image={Path(image_path).name} | wf={wf_file}")
    except Exception as e:
        print(f"[joycaption] queue error: {e}"); return None

    deadline = time.time() + 600
    tick = 0
    result_text = None
    while time.time() < deadline:
        if stop_event and stop_event.is_set(): break
        await asyncio.sleep(1)
        tick += 1
        try:
            hist = await loop.run_in_executor(None, comfy_history, pid)
        except Exception:
            continue
        if pid not in hist: continue

        outputs = hist[pid].get("outputs", {})
        for nid, out in outputs.items():
            for field in ("text", "string", "output", "caption"):
                if field in out:
                    texts = out[field]
                    if isinstance(texts, list) and texts:
                        val = texts[0]
                        if isinstance(val, str) and val.strip():
                            result_text = val; break
                        if isinstance(val, dict) and val.get("text"):
                            result_text = val["text"]; break
                    if isinstance(texts, str) and texts.strip():
                        result_text = texts; break
            if result_text: break
        if result_text:
            print(f"[joycaption] caption ({len(result_text)} chars): {result_text[:200]}")
            await loop.run_in_executor(None, comfy_delete_history, pid)
            return result_text

        status = hist[pid].get("status", {}).get("status_str", "")
        if status == "error":
            print(f"[joycaption] ComfyUI error — pid={pid}")
            await loop.run_in_executor(None, comfy_delete_history, pid)
            return None
        if status == "success":
            print(f"[joycaption] success but no text found. outputs keys: {list(outputs.keys())}")
            for nid, out in outputs.items():
                print(f"  node {nid}: {list(out.keys())}")
            await loop.run_in_executor(None, comfy_delete_history, pid)
            return None

    print(f"[joycaption] timeout after {tick}s")
    await loop.run_in_executor(None, comfy_delete_history, pid)
    return None

async def run_identity_pass(base_image: str, ref_image: str,
                             id_wf_file: str, out_dir: Path,
                             index: int = 0,
                             stop_event: asyncio.Event | None = None) -> str | None:
    loop = asyncio.get_event_loop()
    wf_path = API_DIR / id_wf_file
    if not wf_path.exists(): return None
    with open(wf_path, encoding="utf-8") as f: wf = json.load(f)
    try:
        base_name = await loop.run_in_executor(None, comfy_upload_image, base_image)
        ref_name  = await loop.run_in_executor(None, comfy_upload_image, ref_image)
    except Exception as e:
        print(f"[identity] upload error: {e}"); return None
    load_nodes = sorted([(nid, node) for nid, node in wf.items()
                          if isinstance(node, dict) and node.get("class_type") == "LoadImage"],
                         key=lambda x: str(x[0]))
    inj_base = inj_ref = False
    for nid, node in wf.items():
        if not isinstance(node, dict) or node.get("class_type") != "LoadImage": continue
        t = (node.get("_meta", {}).get("title", "") or "").lower()
        if any(k in t for k in ["source", "base", "input"]) and not inj_base:
            wf[nid]["inputs"]["image"] = base_name; inj_base = True
        elif any(k in t for k in ["ref", "identity", "face"]) and not inj_ref:
            wf[nid]["inputs"]["image"] = ref_name;  inj_ref  = True
    if not inj_base and load_nodes: wf[load_nodes[0][0]]["inputs"]["image"] = base_name
    if not inj_ref  and len(load_nodes) >= 2: wf[load_nodes[1][0]]["inputs"]["image"] = ref_name
    m = detect_mapping(wf)
    if m["seed_node"]: wf[m["seed_node"]]["inputs"][m["seed_field"]] = random.randint(0, 2**53)
    try: pid = await loop.run_in_executor(None, comfy_queue, wf, uuid.uuid4().hex)
    except Exception as e: print(f"[identity] queue error: {e}"); return None
    deadline = time.time() + 300
    while time.time() < deadline:
        if stop_event and stop_event.is_set(): return None
        await asyncio.sleep(2)
        try: hist = await loop.run_in_executor(None, comfy_history, pid)
        except Exception: continue
        if pid in hist:
            for out in hist[pid].get("outputs", {}).values():
                if "images" in out:
                    info = out["images"][0]
                    img  = await loop.run_in_executor(None, comfy_get_image,
                           info["filename"], info.get("subfolder", ""), info.get("type", "output"))
                    out_dir.mkdir(parents=True, exist_ok=True)
                    dst = out_dir / f"id_{index:02d}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}.jpg"
                    dst.write_bytes(img)
                    return str(dst)
            break
    return None

# ── Dialogue overlay (PIL post-process) ───────────────────────────────
def _parse_dialogue_boxes(raw) -> list | None:
    """parse dialogue (str or list) → list of {type, text, character?}"""
    import re as _re, json as _json
    if isinstance(raw, list):
        return raw if raw else None
    raw = (raw or "").strip()
    if not raw:
        return None
    raw = _re.sub(r'^```(?:json)?\s*', '', raw)
    raw = _re.sub(r'\s*```$', '', raw).strip()
    if raw.startswith("["):
        try:
            data = _json.loads(raw)
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    fixed = raw
    if "'" in fixed and '"' not in fixed:
        fixed = fixed.replace("'", '"')
    fixed = _re.sub(r'([{,]\s*)(type|text|character)\s*:', r'\1"\2":', fixed)
    fixed = _re.sub(r'"type"\s*:\s*(narration|speech)\b', r'"type": "\1"', fixed)
    fixed = _re.sub(r',\s*]', ']', fixed)
    m = _re.search(r'(\[[\s\S]*\])', fixed)
    if m:
        try:
            data = _json.loads(m.group(1))
            if isinstance(data, list) and data:
                return data
        except Exception:
            pass
    return None


def _wrap_text(text: str, font, max_width: int, draw) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines, current = [], words[0]
    for w in words[1:]:
        trial = current + " " + w
        bb = draw.textbbox((0, 0), trial, font=font)
        if bb[2] - bb[0] <= max_width:
            current = trial
        else:
            lines.append(current)
            current = w
    lines.append(current)
    return lines


_THAI_TOKENIZER = {"tried": False, "fn": None}
_THAI_LEADING = "เแโใไ"                      # vowels written BEFORE their consonant: never leave one at a line end


def _has_thai(s: str) -> bool:
    return any("\u0e00" <= ch <= "\u0e7f" for ch in s)


def _thai_units(word: str) -> list[str]:
    """Break points inside a Thai 'word' (Thai is written without spaces, so a whole phrase is one
    whitespace-separated chunk and would force a very wide box). Uses PyThaiNLP's dictionary tokenizer when
    it is installed; otherwise falls back to grapheme clusters (a consonant plus its marks), never separating
    a leading vowel (เ แ โ ใ ไ) from the consonant it belongs to."""
    if not _THAI_TOKENIZER["tried"]:
        _THAI_TOKENIZER["tried"] = True
        try:
            from pythainlp.tokenize import word_tokenize
            _THAI_TOKENIZER["fn"] = lambda w: word_tokenize(w, engine="newmm", keep_whitespace=False)
        except Exception:
            _THAI_TOKENIZER["fn"] = None
    fn = _THAI_TOKENIZER["fn"]
    if fn is not None:
        try:
            units = [u for u in fn(word) if u]
            if units and "".join(units) == word:
                return units
        except Exception:
            pass
    units, cur = [], ""
    for ch in word:
        starts = ("\u0e01" <= ch <= "\u0e2e") or ch in _THAI_LEADING          # consonant / leading vowel
        if starts and cur and cur[-1] not in _THAI_LEADING:
            units.append(cur); cur = ""
        cur += ch
    if cur: units.append(cur)
    return units


def _wrap_text_thai(text: str, font, max_width: int, draw) -> list[str]:
    """Like _wrap_text, but a Thai chunk may also break between words (see _thai_units), so Thai narration
    wraps into compact boxes instead of one unbreakable line. Non-Thai text wraps exactly as before."""
    units: list[tuple[str, str]] = []             # (separator before, text)
    for wi, word in enumerate(text.split()):
        parts = _thai_units(word) if (_has_thai(word) and len(word) > 4) else [word]
        for pi, part in enumerate(parts):
            units.append((" " if (wi > 0 and pi == 0) else "", part))
    if not units:
        return [""]
    lines, current = [], units[0][1]
    for sep, part in units[1:]:
        trial = current + sep + part
        if draw.textbbox((0, 0), trial, font=font)[2] - draw.textbbox((0, 0), trial, font=font)[0] <= max_width:
            current = trial
        else:
            lines.append(current)
            current = part
    lines.append(current)
    return lines


def _load_overlay_font(font_size: int):
    from PIL import ImageFont
    import platform, glob, os
    cands = []
    if platform.system() == "Windows":
        win = os.environ.get("WINDIR", os.path.join(os.environ.get("SystemDrive", "C:"), "\\Windows")) + "/Fonts"
        cands = [
            f"{win}/NotoSansThai-Regular.ttf", f"{win}/NotoSans-Regular.ttf",
            f"{win}/THSarabunNew.ttf", f"{win}/Tahoma.ttf", f"{win}/arial.ttf",
        ] + glob.glob(f"{win}/Noto*.ttf")
    elif platform.system() == "Darwin":
        cands = ["/System/Library/Fonts/Supplemental/Arial.ttf", "/Library/Fonts/Arial.ttf"]
    else:
        cands = [
            "/usr/share/fonts/truetype/noto/NotoSansThai-Regular.ttf",
            "/usr/share/fonts/truetype/thai/Garuda.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        ]
    for fp in cands:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, font_size)
            except Exception:
                continue
    return ImageFont.load_default()


def _measure_overlay_block_height(image_path: str, text: str, style: str, font_size: int = 32) -> int:
    """Legacy measure-only helper (height of a single caption box). No longer used for narration /
    subtitle coordination — _subtitle_bar_metrics reports the subtitle bar's exact area instead.
    Returns 0 on any failure."""
    try:
        from PIL import Image, ImageDraw
        img = Image.open(image_path)
        W, H = img.size
        draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        font = _load_overlay_font(font_size)
        pad_y = int(H * 0.012)
        max_text_w = int(W * 0.88)
        lines = _wrap_text(str(text or "").strip(), font, max_text_w, draw)
        lh = []
        for ln in lines:
            bb = draw.textbbox((0, 0), ln, font=font)
            lh.append(bb[3] - bb[1])
        lg = max(2, int(font_size * 0.25))
        text_h = sum(lh) + lg * (len(lines) - 1)
        box_h = text_h + pad_y * 2
        return box_h
    except Exception:
        return 0


# ── Auto-placement of the PIL narration box (empty space instead of a fixed spot) ──────────────
# Optional person detection (Ultralytics YOLO). It is never required and never downloads anything on its own:
# it is used only when `ultralytics` is importable AND a local weights file exists (env var
# WORKFLOWPAD_YOLO_WEIGHTS, the per-user models folder, or models/ next to the app — see _find_weights).
# Without it the box is placed by image clutter alone.
_YOLO = {"model": None, "tried": False}


def _import_yolo():
    """Import Ultralytics' YOLO class. Its anonymous usage analytics ("sync") are switched off first:
    this app runs entirely on the user's machine and must not phone home as a side effect of drawing text."""
    from ultralytics import YOLO, settings
    try:
        if settings.get("sync"):
            settings.update({"sync": False})
    except Exception:
        pass
    return YOLO


# ── Model storage / discovery ──────────────────────────────────────────────────────────────────
# Models are optional and downloaded only when the user asks (Install Models). They live in a per-user
# folder so a read-only / replaced app folder or a frozen EXE never needs write access to its own directory.
YOLO_WEIGHTS_NAME = "yolov8n.pt"
_YOLO_FALLBACK_URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n.pt"
# Optional second model: person POSE (nose / eyes / ears keypoints) — gives real face boxes so the
# narration box can avoid faces specifically, not just "the top 45% of a person".
YOLO_POSE_WEIGHTS_NAME = "yolov8n-pose.pt"
_YOLO_POSE_URL = "https://github.com/ultralytics/assets/releases/download/v8.3.0/yolov8n-pose.pt"
_YOLO_POSE = {"model": None, "tried": False}


def _app_roots() -> list:
    """Folders that may hold bundled models: next to the EXE / PyInstaller data dir when frozen, else the app root."""
    import sys
    if getattr(sys, "frozen", False):
        roots = [Path(sys.executable).resolve().parent]
        mp = getattr(sys, "_MEIPASS", "")
        if mp:
            roots.append(Path(mp))
        return roots
    return [Path(__file__).resolve().parent.parent]


def user_models_dir() -> Path:
    """<LOCALAPPDATA>/WorkflowPad/models, with a writable fallback when that variable is missing or unusable."""
    import os, tempfile
    bases = []
    la = os.environ.get("LOCALAPPDATA", "").strip()
    if la:
        bases.append(Path(la) / "WorkflowPad" / "models")
    try:
        bases.append(Path.home() / ".workflowpad" / "models")
    except Exception:
        pass
    bases.append(Path(tempfile.gettempdir()) / "WorkflowPad" / "models")
    for b in bases:
        try:
            b.mkdir(parents=True, exist_ok=True)
            probe = b / f".w{os.getpid()}"
            probe.write_bytes(b"")
            probe.unlink()
            return b
        except Exception:
            continue
    return bases[0]


def _find_weights(env_var: str, names: tuple) -> str | None:
    """env override → user models dir → bundled models dir → legacy paths. Existing files only; never moves or deletes."""
    import os
    cands = []
    env = os.environ.get(env_var, "").strip()
    if env:
        cands.append(Path(env))
    cands.append(user_models_dir() / names[0])
    roots = _app_roots()
    for r in roots:
        cands.append(r / "models" / names[0])
    for name in names:                                   # legacy: alternative names + app root itself
        for r in roots:
            cands += [r / "models" / name, r / name]
    for c in cands:
        if c.is_file():
            return str(c)
    return None


def _find_yolo_weights() -> str | None:
    return _find_weights("WORKFLOWPAD_YOLO_WEIGHTS", (YOLO_WEIGHTS_NAME, "yolo11n.pt", "yolov8s.pt"))


def _find_pose_weights() -> str | None:
    return _find_weights("WORKFLOWPAD_YOLO_POSE_WEIGHTS", (YOLO_POSE_WEIGHTS_NAME, "yolo11n-pose.pt", "yolov8s-pose.pt"))


# ── Runtime status (Installed ≠ Ready) ─────────────────────────────────────────────────────────
# library: ultralytics really imports.  person / face: ready | missing | invalid | unavailable.
# "ready" means the file was loaded by Ultralytics (task checked); a load is cached per (path, size, mtime) so
# opening the dialog never reloads a model. Status checks never download and never touch ComfyUI.
_LIB = {"state": None, "err": ""}
_MODEL_CHECK: dict = {}
_KIND = {"person": ("detect", _find_yolo_weights), "face": ("pose", _find_pose_weights)}


def _library_state() -> str:
    if _LIB["state"] is None:
        try:
            _import_yolo()
            _LIB["state"] = "ready"
        except Exception as exc:                          # ImportError, broken torch DLLs, ...
            _LIB["state"], _LIB["err"] = "unavailable", str(exc)
    return _LIB["state"]


def _check_model_file(path: str, task: str) -> str:
    """"ready" | "invalid" for an existing file. Loads it with Ultralytics once per file version."""
    try:
        st = Path(path).stat()
    except OSError:
        return "invalid"
    key = (path, st.st_size, st.st_mtime_ns, task)
    if key in _MODEL_CHECK:
        return _MODEL_CHECK[key]
    res = "invalid"
    if st.st_size >= 1_000_000:                           # a real checkpoint is megabytes; avoids loading junk
        try:
            m = _import_yolo()(path)
            res = "ready" if getattr(m, "task", task) == task else "invalid"
        except Exception as exc:
            print(f"[yolo] {path} did not load: {exc}")
    _MODEL_CHECK[key] = res
    return res


def yolo_runtime_status() -> dict:
    """{"library", "person", "face", "paths"}. Blocking (first call imports ultralytics) — run it in an executor."""
    lib = _library_state()
    out = {"library": lib, "person": "unavailable", "face": "unavailable", "paths": {}}
    for kind, (task, finder) in _KIND.items():
        p = finder()
        out["paths"][kind] = p
        if lib != "ready":
            continue
        out[kind] = "missing" if not p else _check_model_file(p, task)
    return out


def yolo_status() -> str:
    """Legacy summary: "ready" (library + a usable person model), "no_weights" or "no_lib"."""
    s = yolo_runtime_status()
    if s["library"] != "ready":
        return "no_lib"
    return "ready" if s["person"] == "ready" else "no_weights"


# ── Installation ───────────────────────────────────────────────────────────────────────────────
_INSTALL = {"person": (YOLO_WEIGHTS_NAME, _YOLO_FALLBACK_URL), "face": (YOLO_POSE_WEIGHTS_NAME, _YOLO_POSE_URL)}


def install_yolo_model(kind: str, on_progress=None) -> tuple[bool, str]:
    """Download one model into the user models dir. Blocking — call from an executor, only after the user chose
    Install. Flow: temp file → Ultralytics load check (+ task) → atomic rename. A working model is never replaced by a
    bad download, and the temp file is always removed. on_progress(done_bytes, total_bytes_or_0)."""
    import os
    name, url = _INSTALL[kind]
    task = _KIND[kind][0]
    if _library_state() != "ready":
        return False, "the detection library is not available in this build"
    target = user_models_dir() / name
    tmp = target.with_name(target.stem + ".part.pt")
    try:
        if target.is_file() and _check_model_file(str(target), task) == "ready":
            return True, str(target)
        with urllib.request.urlopen(url, timeout=30) as r, open(tmp, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if on_progress:
                    on_progress(done, total)
        if total and done != total:
            return False, "download was incomplete"
        if _check_model_file(str(tmp), task) != "ready":
            return False, "the downloaded file is not a valid model"
        os.replace(tmp, target)
        _MODEL_CHECK.clear()                              # file identity changed
        if kind == "person":
            _YOLO["model"] = None; _YOLO["tried"] = False  # let the next overlay load it
        else:
            _YOLO_POSE["model"] = None; _YOLO_POSE["tried"] = False
        return True, str(target)
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _detect_subject_boxes(image_path: str):
    """([person boxes], [other object boxes]) in pixels — boxes are (x0, y0, x1, y1) — or None when no
    detector is available. Objects are any other detected COCO class (props the box should avoid)."""
    if _YOLO["model"] is None:
        if _YOLO["tried"]:
            return None
        _YOLO["tried"] = True
        try:
            YOLO = _import_yolo()
        except Exception:
            print("[narration auto-place] ultralytics not installed — placing by image clutter only")
            return None
        weights = _find_yolo_weights()
        if not weights:
            print("[narration auto-place] no local YOLO weights found — placing by image clutter only")
            return None
        try:
            _YOLO["model"] = YOLO(weights)
        except Exception as exc:
            print(f"[narration auto-place] YOLO load failed: {exc}")
            return None
    try:
        res = _YOLO["model"].predict(image_path, conf=0.30, verbose=False)
        xyxy = res[0].boxes.xyxy.tolist()
        cls = [int(c) for c in res[0].boxes.cls.tolist()]
        people = [tuple(float(v) for v in b) for b, c in zip(xyxy, cls) if c == 0]
        objects = [tuple(float(v) for v in b) for b, c in zip(xyxy, cls) if c != 0]
        return people, objects, _detect_face_boxes(image_path)
    except Exception as exc:
        print(f"[narration auto-place] detection failed: {exc}")
        return None


def _detect_face_boxes(image_path: str) -> list:
    """Face boxes (x0, y0, x1, y1) from the optional pose model's head keypoints (nose, eyes, ears), grown
    to cover forehead/hair and chin. [] when the pose model is absent or finds nobody — never raises."""
    if _YOLO_POSE["model"] is None:
        if _YOLO_POSE["tried"]:
            return []
        _YOLO_POSE["tried"] = True
        weights = _find_pose_weights()
        if not weights:
            print("[narration auto-place] no pose weights — faces estimated from the person's upper body")
            return []
        try:
            _YOLO_POSE["model"] = _import_yolo()(weights)
        except Exception as exc:
            print(f"[narration auto-place] pose model load failed: {exc}")
            return []
    try:
        res = _YOLO_POSE["model"].predict(image_path, conf=0.25, verbose=False)[0]
        if res.keypoints is None:
            return []
        kxy = res.keypoints.xy.tolist()
        kcf = res.keypoints.conf.tolist() if res.keypoints.conf is not None else None
        faces = []
        for pi, pts in enumerate(kxy):
            vis = [(x, y) for ki, (x, y) in enumerate(pts[:5])
                   if x > 0 and y > 0 and (kcf is None or kcf[pi][ki] > 0.35)]
            if len(vis) < 2:
                continue
            xs, ys = [v[0] for v in vis], [v[1] for v in vis]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            side = max(w, h * 1.3, 1.0)                       # head size from the visible landmarks
            cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            faces.append((cx - side * 0.95, cy - side * 1.1, cx + side * 0.95, cy + side * 1.0))
        return faces
    except Exception as exc:
        print(f"[narration auto-place] pose detection failed: {exc}")
        return []


def _rect_overlap(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if (w > 0 and h > 0) else 0.0


_LAST_NARRATION_PLACEMENT: dict = {}     # diagnostics of the last placement (tests / debugging only)

# Human safety is a FEASIBILITY test, never a score term. A candidate that covers a face or (in the first two
# passes) a person is not "expensive", it is rejected — so no amount of quiet background can make covering a
# character the better choice. Contact within this fraction is tolerated (a box touching a YOLO rectangle's
# edge by a pixel or two is not an overlap).
_FACE_EPS = 0.012       # fraction of the (grown) face zone the box may touch
_PERSON_EPS = 0.012     # fraction of the BOX area that may lie inside person zones


def _narration_variants(draw, text: str, font_size: int, W: int, H: int, pad_x: int, pad_y: int,
                        compact: bool = False):
    """Candidate box geometries for the narration, measured BEFORE choosing where it goes.

    Normal set: 5 widths (about 26-68% of the image) x 3 font sizes (100%, 88%, 76% of the requested one),
    each kept only if it needs at most 3 lines (if nothing does, the smallest font with any line count).
    Compact set (used when the normal set cannot avoid a person): the normal set PLUS narrower boxes
    (about 16-44% wide) at the smallest sizes (76% and 70%), up to 4 lines — a smaller card is better than
    one that covers a character.

    Line height is uniform and measured from a reference string with tall Thai marks and descenders,
    so tone marks / vowels above and below the baseline are never clipped and lines sit evenly."""
    min_fs = max(10, int(font_size * 0.76))
    sizes = []
    for f in (1.0, 0.88, 0.76):
        v = max(min_fs, int(round(font_size * f)))
        if v not in sizes:
            sizes.append(v)
    variants, seen = [], set()

    def _add(fs, frac, max_lines):
        font = _fonts[fs]
        lines = _wrap_text_thai(text, font, int(W * frac), draw)
        if max_lines and len(lines) > max_lines:
            return
        rb, lg = _metrics[fs]
        top_off, line_h = rb[1], rb[3]
        lw = [draw.textbbox((0, 0), ln, font=font)[2] for ln in lines]
        box_w = min(max(lw) + pad_x * 2, int(W * 0.94))
        box_h = line_h * len(lines) + lg * (len(lines) - 1) - top_off + pad_y * 2
        if (box_w, box_h, fs) in seen:
            return
        seen.add((box_w, box_h, fs))
        variants.append({"lines": lines, "lw": lw, "line_h": line_h, "top_off": top_off, "lg": lg,
                         "box_w": box_w, "box_h": box_h, "font": font, "fs": fs,
                         "shrink": 1.0 - fs / float(font_size)})

    all_sizes = list(sizes)
    small = max(10, int(round(font_size * 0.70)))
    if compact and small not in all_sizes:
        all_sizes.append(small)
    _fonts = {fs: _load_overlay_font(fs) for fs in all_sizes}
    _metrics = {fs: (draw.textbbox((0, 0), "ฟ้ป็ูÁgjyŞ", font=_fonts[fs]), max(2, int(fs * 0.12))) for fs in all_sizes}

    for strict in (True, False):                 # second pass (any line count) only if nothing fits in 3
        for fs in sizes:
            for frac in (0.26, 0.34, 0.44, 0.56, 0.68):
                if strict:
                    _add(fs, frac, 3)
                elif fs == min_fs:
                    _add(fs, frac, 0)
        if variants:
            break
    if compact:
        for fs in (min_fs, small):
            for frac in (0.16, 0.20, 0.25, 0.30, 0.36, 0.44):
                _add(fs, frac, 4)
    return variants


def _auto_place_narration(img, text: str, font_size: int, draw, W: int, H: int,
                          pad_x: int, pad_y: int, reserved_bottom: int, subjects=None):
    """Choose where the PIL narration box goes, from the rendered image itself (no prompt/keyword logic,
    no fixed corner or edge). `img` must be the pristine render (no overlay text yet).

    HUMAN SAFETY FIRST, AESTHETICS SECOND — three ordered passes, each stopping at its first success:

      A  normal   preferred variants, 3% margins, 7x7 grid (+ positions snapped to the edges of detected
                  people). A candidate is feasible only if it touches no face zone, no person zone (the
                  WHOLE YOLO person box, slightly grown) and not the subtitle zone. The feasible ones are
                  ranked by objects, saliency, clutter, font shrink, box size.
      B  compact  same rules, but narrower / smaller / up-to-4-line boxes, smaller margins, 9x9 grid.
      C  relaxed  only when no human-free spot exists at all: faces stay protected if physically possible,
                  then the box is placed to MINIMISE human overlap (larger / more central people weigh
                  more) — a messy spot with 5% overlap beats a clean one with 25%. Only after that
                  objects / saliency / clutter. The subtitle zone stays excluded unless nothing fits.

    The YOLO person boxes decide feasibility. The saliency / skin mask only refines ranking and diagnostics:
    it can never make a candidate that overlaps a person acceptable. Without a detector there are no person
    or face zones, so every pass ranks purely by saliency / skin / clutter.

    reserved_bottom is the height (px) of the subtitle bar along the bottom edge (a forbidden zone, not an
    offset). Returns a dict (geometry, font, where, search_pass, metrics) or None if no box fits at all."""
    from PIL import ImageFilter, ImageStat
    try:
        import numpy as np
    except Exception:
        np = None

    normal_variants = _narration_variants(draw, text, font_size, W, H, pad_x, pad_y)
    if not normal_variants:
        return None
    people, objects = (subjects[0], subjects[1]) if subjects else ([], [])
    faces = subjects[2] if subjects and len(subjects) > 2 else []
    detector = subjects is not None

    # ── protected zones ─────────────────────────────────────────────────────────────────────────────
    # The YOLO person box is the protected area (the WHOLE body, not just the upper part). It is grown by a
    # clearance so a card does not hug a silhouette: full clearance in the normal pass, 35% of it in the
    # compact / relaxed passes (a tight pocket beats covering someone, but the card still must not touch).
    def build_zones(clear: float) -> dict:
        pz, uz, wt = [], [], []
        for x0, y0, x1, y1 in people:
            px, py = ((x1 - x0) * 0.06 + W * 0.01) * clear, ((y1 - y0) * 0.06 + H * 0.01) * clear
            pz.append((max(0, x0 - px), max(0, y0 - py), min(W, x1 + px), min(H, y1 + py)))
            uz.append((max(0, x0 - px), max(0, y0 - py), min(W, x1 + px), min(H, y0 + (y1 - y0) * 0.45 + py)))
            cx, cy = (x0 + x1) / 2 / W, (y0 + y1) / 2 / H                           # larger / more central = dominant
            centred = 1.0 - min(1.0, ((cx - 0.5) ** 2 + (cy - 0.5) ** 2) ** 0.5 / 0.7)
            wt.append(1.0 + 2.0 * ((x1 - x0) * (y1 - y0)) / float(W * H) + 0.5 * centred)
        fz = []
        for x0, y0, x1, y1 in faces:                     # a box hugging a face is as bad as one covering it
            gx, gy = (x1 - x0) * 0.35, (y1 - y0) * 0.30
            fz.append((max(0, x0 - gx), max(0, y0 - gy), min(W, x1 + gx), min(H, y1 + gy)))
        return {"person": pz, "upper": uz, "weight": wt, "face": fz}

    object_zones = []
    for x0, y0, x1, y1 in objects:
        px, py = (x1 - x0) * 0.05 + W * 0.005, (y1 - y0) * 0.05 + H * 0.005
        object_zones.append((max(0, x0 - px), max(0, y0 - py), min(W, x1 + px), min(H, y1 + py)))
    ZN, ZT = build_zones(1.0), build_zones(0.35)
    person_zones = ZN["person"]
    # Reach band: neither YOLO nor the pose model sees every limb or held item (a robotic arm, a sword blade
    # sticking out sideways). Arms and weapons live at shoulder-to-hip height and reach about one body
    # height to each side, so that band around every person is a SOFT no-go: a candidate there is allowed
    # (the hard zones above still decide feasibility) but ranks behind comparable candidates outside it.
    reach_bands = []
    for x0, y0, x1, y1 in people:
        h = y1 - y0
        reach_bands.append((max(0, x0 - 0.5 * h), y0 + 0.30 * h, min(W, x1 + 0.5 * h), y0 + 0.72 * h))
    bar = (0, H - reserved_bottom, W, H) if reserved_bottom > 0 else None

    # ── image analysis on a small copy (cheap): edges, grey spread, salient / skin mask ─────────────
    scale = 320.0 / max(W, 1)
    sw, sh = max(1, int(W * scale)), max(1, int(H * scale))
    small = img.convert("RGB").resize((sw, sh))
    grey = small.convert("L")
    edges = grey.filter(ImageFilter.FIND_EDGES)
    sal = None
    if np is not None:
        # Two cues OR-ed together: frequency-tuned saliency (distance of each lightly blurred pixel from the
        # image's mean colour) and skin tones (YCbCr range). Ranking / diagnostics only.
        arr = np.asarray(small.filter(ImageFilter.GaussianBlur(1.5)), dtype=np.float32)
        ft = np.linalg.norm(arr - arr.reshape(-1, 3).mean(axis=0), axis=2)
        fg = ft > (ft.mean() + 0.6 * ft.std())
        rgb = np.asarray(small, dtype=np.float32)
        yy = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
        cb = 128.0 - 0.168736 * rgb[..., 0] - 0.331264 * rgb[..., 1] + 0.5 * rgb[..., 2]
        cr = 128.0 + 0.5 * rgb[..., 0] - 0.418688 * rgb[..., 1] - 0.081312 * rgb[..., 2]
        skin = (yy > 50) & (cb > 77) & (cb < 127) & (cr > 133) & (cr < 173)
        sal = (fg | skin).astype(np.float32)
    pmask = None                                         # salient pixels inside a person box (diagnostics / tie-break)
    if sal is not None and person_zones:
        pmask = np.zeros_like(sal)
        for zx0, zy0, zx1, zy1 in person_zones:
            pmask[max(0, int(zy0 * scale)):min(sh, int(zy1 * scale) + 1),
                  max(0, int(zx0 * scale)):min(sw, int(zx1 * scale) + 1)] = 1.0
        pmask = pmask * sal

    def _sbox(rect):
        x0, y0 = max(0, int(rect[0] * scale)), max(0, int(rect[1] * scale))
        return x0, y0, min(sw, max(int(rect[2] * scale), x0 + 1)), min(sh, max(int(rect[3] * scale), y0 + 1))

    def clutter(rect) -> float:
        b = _sbox(rect)
        if b[2] <= b[0] or b[3] <= b[1]:
            return 0.0
        e = ImageStat.Stat(edges.crop(b)).mean[0] / 40.0
        v = ImageStat.Stat(grey.crop(b)).stddev[0] / 70.0
        return min(1.0, 0.5 * min(1.0, e) + 0.5 * min(1.0, v))

    def mask_mean(mask, rect) -> float:
        if mask is None:
            return 0.0
        x0, y0, x1, y1 = _sbox(rect)
        return float(mask[y0:y1, x0:x1].mean()) if x1 > x0 and y1 > y0 else 0.0

    def frac(rect, zones) -> float:
        area = float((rect[2] - rect[0]) * (rect[3] - rect[1]))
        return min(1.0, sum(_rect_overlap(rect, z) for z in zones) / area) if area > 0 and zones else 0.0

    def covered(rect, zones) -> float:
        """Largest fraction of any single zone that the box hides (for small things a big box can bury)."""
        best = 0.0
        for z in zones:
            za = float((z[2] - z[0]) * (z[3] - z[1]))
            if za > 0:
                best = max(best, _rect_overlap(rect, z) / za)
        return min(1.0, best)

    def person_weighted(rect, Z) -> float:
        area = float((rect[2] - rect[0]) * (rect[3] - rect[1]))
        if area <= 0 or not Z["person"]:
            return 0.0
        return sum(w * _rect_overlap(rect, z) for w, z in zip(Z["weight"], Z["person"])) / area

    def human_geometry(rect, Z) -> dict:                 # cheap, no pixels — decides feasibility
        return {"faces": covered(rect, Z["face"]), "people": frac(rect, Z["person"]),
                "person_w": person_weighted(rect, Z),
                "upper": max(frac(rect, Z["upper"]), covered(rect, Z["upper"])),
                "bar": frac(rect, [bar]) if bar else 0.0}

    # remaining 'salient' pixels (light shafts, shadows, bright floor) are mostly background contrast once a
    # detector accounts for the people, so they weigh less
    sal_w = 250.0 if person_zones else 700.0

    snaps_x, snaps_y = [], []                            # positions flush against the protected zones
    for z in (ZT["person"] + ZT["face"])[:8]:
        snaps_x += [("after", z[2] + 2), ("before", z[0] - 2)]
        snaps_y += [("after", z[3] + 2), ("before", z[1] - 2)]

    def run_pass(name, variants, margin_x, margin_y, steps, hard_person, hard_face, hard_reserve, Z):
        cands, generated = [], 0
        for vi, v in enumerate(variants):
            bw, bh = v["box_w"], v["box_h"]
            x_lo, x_hi = margin_x, W - bw - margin_x
            y_lo, y_hi = margin_y, H - bh - margin_y - (reserved_bottom if hard_reserve else 0)
            if x_hi < x_lo or y_hi < y_lo:
                continue                                 # does not fit with margins
            xs = {int(x_lo + (x_hi - x_lo) * i / (steps - 1)): i for i in range(steps)}
            ys = {int(y_lo + (y_hi - y_lo) * i / (steps - 1)): i for i in range(steps)}
            for kind, pos in snaps_x:
                x = int(pos if kind == "after" else pos - bw)
                if x_lo <= x <= x_hi: xs.setdefault(x, -1)
            for kind, pos in snaps_y:
                y = int(pos if kind == "after" else pos - bh)
                if y_lo <= y <= y_hi: ys.setdefault(y, -1)
            for y, yi in ys.items():
                for x, xi in xs.items():
                    generated += 1
                    rect = (x, y, x + bw, y + bh)
                    g = human_geometry(rect, Z)
                    if hard_face and g["faces"] > _FACE_EPS: continue            # faces: never in A / B
                    if hard_person and g["people"] > _PERSON_EPS: continue       # people: never in A / B
                    m = {**g, "objects": frac(rect, object_zones), "salient": mask_mean(sal, rect),
                         "clutter": clutter(rect), "body": mask_mean(pmask, rect), "reach": frac(rect, reach_bands)}
                    rest = (250.0 * m["objects"] + 350.0 * m["reach"] + sal_w * m["salient"] + 200.0 * m["clutter"]
                            + 120.0 * (v["shrink"] / 0.24) + 40.0 * (bw * bh) / float(W * H)
                            + (40.0 if len(v["lines"]) > 3 else 0.0)
                            + 1.0 * y / float(H))        # tie-break only: reads slightly better higher up
                    cands.append({**v, "bx": x, "by": y, "m": m, "rest": rest, "xi": xi, "yi": yi,
                                  "variant": vi, "steps": steps})
        return cands, generated

    def finish(cands, generated, name, key):
        cands.sort(key=key)
        best = cands[0]
        fx = best["bx"] / float(max(1, W - best["box_w"]))
        fy = best["by"] / float(max(1, H - best["box_h"]))
        ynm = "top" if fy < 0.2 else "upper" if fy < 0.4 else "middle" if fy < 0.6 else "lower" if fy < 0.8 else "bottom"
        xnm = "left" if fx < 0.2 else "centre-left" if fx < 0.4 else "centre" if fx < 0.6 else "centre-right" if fx < 0.8 else "right"
        best.update(where=f"{ynm}-{xnm}", search_pass=name, reserved_bottom=reserved_bottom, rank=1,
                    counts={"people": len(people), "faces": len(faces), "objects": len(objects)},
                    n_candidates=len(cands), n_generated=generated, detector=detector,
                    overlap=best["m"]["people"], score=best["rest"])
        return best

    mxn, myn = int(W * 0.03), max(pad_y * 2, int(H * 0.03))
    # PASS A — normal: human-free spots only, ranked by aesthetics
    cands, gen = run_pass("normal", normal_variants, mxn, myn, 7, True, True, True, ZN)
    if cands:
        return finish(cands, gen, "normal", lambda c: c["rest"])

    # PASS B — compact: still human-free, but a smaller / narrower card is allowed
    compact_variants = _narration_variants(draw, text, font_size, W, H, pad_x, pad_y, compact=True)
    cands, gen = run_pass("compact", compact_variants, int(W * 0.015), max(pad_y, int(H * 0.015)), 9,
                          True, True, True, ZT)
    if cands:
        return finish(cands, gen, "compact", lambda c: c["rest"])

    # PASS C — relaxed: overlap is unavoidable. Faces stay protected if physically possible; minimise human
    # overlap first (weighted by how dominant each person is), aesthetics only afterwards.
    rmx, rmy = max(4, int(W * 0.008)), max(4, int(H * 0.008))
    for hard_reserve in (True, False):
        if not hard_reserve and reserved_bottom <= 0:
            break
        cands, gen = run_pass("relaxed", compact_variants, rmx, rmy, 11, False, False, hard_reserve, ZT)
        if cands:
            break
    if not cands:
        return None
    safe = [c for c in cands if c["m"]["faces"] <= _FACE_EPS]
    pool = safe or cands                                  # face overlap only when no face-safe spot exists at all
    pass_name = "relaxed" if hard_reserve else "relaxed+soft-reserve"

    def key_c(c):
        m = c["m"]
        return (round(m["bar"], 3) if not hard_reserve else 0.0,      # never trade the subtitle for a person
                round(m["faces"], 3), round(m["person_w"], 3), round(m["upper"], 3),
                round(m["body"], 3), c["rest"])
    return finish(pool, gen, pass_name, key_c)


def _safe_detect_subjects(image_path: str):
    """People / objects / faces of the image at image_path, or None (no detector, or it failed). Must be
    called while the file is still the pristine render — before any overlay text is saved into it."""
    try:
        return _detect_subject_boxes(image_path)
    except Exception as exc:
        print(f"[narration auto-place] detector unavailable: {exc}")
        return None


def _place_narration(img, text: str, font_size: int, draw, W: int, H: int,
                     reserved_bottom: int, subjects):
    """Where the PIL narration card goes — always from the image itself, never from a position setting.
    Delegates to _auto_place_narration's staged search (normal -> compact -> relaxed), humans first.
    Returns None only if no box fits inside the image at all."""
    pad_x, pad_y = max(8, int(W * 0.018)), max(6, int(H * 0.012))
    try:
        pl = _auto_place_narration(img, text, font_size, draw, W, H, pad_x, pad_y, reserved_bottom, subjects)
    except Exception as exc:
        print(f"[narration auto-place] failed: {exc}")
        pl = None
    if pl is None:
        print("[narration auto-place] no box fits inside the image")
        return None
    pl["pad_y"] = pad_y
    _LAST_NARRATION_PLACEMENT.clear()
    _LAST_NARRATION_PLACEMENT.update(
        {k: pl[k] for k in ("where", "bx", "by", "box_w", "box_h", "score", "overlap", "m", "rank",
                            "n_candidates", "n_generated", "detector", "fs", "search_pass", "reserved_bottom",
                            "counts")},
        lines=len(pl["lines"]), size=(W, H),
        person_overlap=pl["m"]["people"], face_overlap=pl["m"]["faces"], upper_overlap=pl["m"]["upper"],
        object_overlap=pl["m"]["objects"], saliency=pl["m"]["salient"], clutter=pl["m"]["clutter"])
    print(f"[narration auto-place]\n  pass={pl['search_pass']}\n  where={pl['where']}\n"
          f"  person={pl['m']['people']:.3f}\n  face={pl['m']['faces']:.3f}\n"
          f"  candidates={pl['n_candidates']} (of {pl['n_generated']} generated)\n"
          f"  detector={'yes' if subjects is not None else 'no'} lines={len(pl['lines'])} fs={pl['fs']}")
    return pl


def _draw_narration_card(draw, pl) -> None:
    bx, by, box_w, box_h = pl["bx"], pl["by"], pl["box_w"], pl["box_h"]
    draw.rounded_rectangle([bx, by, bx + box_w, by + box_h], radius=12,
                           fill=(0, 0, 0, 175), outline=(255, 255, 255, 130), width=2)
    ty = by + pl["pad_y"] - pl["top_off"]
    for i, ln in enumerate(pl["lines"]):
        tx = bx + (box_w - pl["lw"][i]) // 2
        draw.text((tx + 1, ty + 1), ln, font=pl["font"], fill=(0, 0, 0, 200))
        draw.text((tx, ty), ln, font=pl["font"], fill=(255, 255, 255, 255))
        ty += pl["line_h"] + pl["lg"]


def _subtitle_bar_metrics(draw, text: str, W: int, H: int, font_size: int) -> dict:
    """Geometry of the bottom subtitle bar (measure only — nothing is drawn)."""
    font = _load_overlay_font(font_size)
    pad_y = int(H * 0.012)
    lines = _wrap_text(text.strip(), font, int(W * 0.88), draw)
    lw, lh = [], []
    for ln in lines:
        bb = draw.textbbox((0, 0), ln, font=font)
        lw.append(bb[2] - bb[0]); lh.append(bb[3] - bb[1])
    lg = max(2, int(font_size * 0.25))
    text_h = sum(lh) + lg * (len(lines) - 1)
    return {"font": font, "lines": lines, "lw": lw, "lh": lh, "lg": lg, "text_h": text_h,
            "bar_h": text_h + pad_y * 2 + pad_y * 2}


def _draw_subtitle_bar(draw, W: int, H: int, m: dict, y_offset: int = 0) -> None:
    """The subtitle bar: full-width, fading dark strip anchored to the bottom edge (y_offset lifts it)."""
    bar_h, text_h, lines, lw, lh, lg, font = (m["bar_h"], m["text_h"], m["lines"], m["lw"], m["lh"],
                                              m["lg"], m["font"])
    by_top = H - bar_h - y_offset
    for row in range(bar_h):
        rel = row / max(bar_h, 1)
        if rel < 0.15:   alpha = int(200 * (rel / 0.15))
        elif rel > 0.85: alpha = int(200 * ((1 - rel) / 0.15))
        else:            alpha = 200
        draw.line([(0, by_top + row), (W, by_top + row)], fill=(0, 0, 0, alpha))
    ty = by_top + (bar_h - text_h) // 2
    for i, ln in enumerate(lines):
        tx = (W - lw[i]) // 2
        draw.text((tx + 1, ty + 1), ln, font=font, fill=(0, 0, 0, 180))
        draw.text((tx, ty), ln, font=font, fill=(255, 255, 255, 255))
        ty += lh[i] + lg


def apply_dual_text_overlay(image_path: str, narration_text: str, subtitle_text: str,
                             style: str, position: str = "bottom", font_size: int = 32) -> bool:
    """TEXT-CHANNEL-A1: draw narration and subtitle/dialogue as two independent PIL channels. Field
    presence decides whether each draws: narration -> adaptive narration card (narration_box_pil),
    subtitle_text -> fixed bottom subtitle bar, both -> both, neither -> no-op (returns True).

    Order matters: the file is read ONCE and stays pristine while it is analysed. Subjects (YOLO / pose)
    are detected on the untouched render, the subtitle bar is only MEASURED (its area becomes a reserved
    zone the narration avoids), the narration is placed from the original image, then both overlays are
    drawn onto one canvas and the image is saved once. `style` and `position` are accepted for
    compatibility; neither affects placement."""
    narration_text = clean_overlay_text(narration_text)      # blank / zero-width / punctuation-only -> nothing drawn
    subtitle_text = clean_overlay_text(subtitle_text)
    if not narration_text and not subtitle_text:
        return True
    try:
        from PIL import Image, ImageDraw
        subjects = _safe_detect_subjects(image_path) if narration_text else None   # pristine file
        img = Image.open(image_path).convert("RGBA")
        W, H = img.size
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        ok = True
        sub_m, reserved = None, 0
        if subtitle_text:
            sub_m = _subtitle_bar_metrics(draw, subtitle_text, W, H, font_size)
            reserved = sub_m["bar_h"] + max(4, int(H * 0.008))       # bar + a small gap: forbidden zone
        if narration_text:
            pl = _place_narration(img, narration_text, font_size, draw, W, H, reserved, subjects)
            if pl is not None:
                pl["subtitle_reserved"] = reserved
                _LAST_NARRATION_PLACEMENT["subtitle_reserved"] = reserved
                _draw_narration_card(draw, pl)
            else:
                ok = False
        if sub_m is not None:
            _draw_subtitle_bar(draw, W, H, sub_m)
        Image.alpha_composite(img, overlay).convert("RGB").save(image_path, quality=95)
        return ok
    except Exception as exc:
        print(f"[dual overlay] {exc}")
        return False


def apply_dialogue_overlay(
    image_path: str,
    text,
    style: str,          # "caption_box" | "subtitle_bar" | "narration_box" | "narration_box_pil"
    position: str,       # "top" | "bottom"
    font_size: int = 32,
    y_offset: int = 0,   # compat: lifts caption_box / narration_box / subtitle_bar off the bottom edge;
                         # for narration_box_pil it is RESERVED bottom space to avoid (not a position offset)
) -> bool:
    """วาด caption / subtitle / multi narration boxes ทับรูปด้วย PIL (รองรับ text wrap + list dialogue)

    position ถูกบังคับเป็น "bottom" เสมอ: กล่องที่ขอบบนไปบังหัว/ใบหน้าของตัวละครซึ่งเป็นส่วนที่
    สำคัญที่สุดของภาพ ค่าที่ส่งเข้ามาจึงถูกละทิ้ง (ผู้เรียกทุกจุดยังส่งได้เหมือนเดิม ไม่ต้องแก้)

    narration_box_pil ignores `position`: its place comes only from analysing the image (see
    _place_narration). For it, y_offset means "this many pixels along the bottom are reserved" and is
    excluded from placement. For the bottom-anchored legacy modes y_offset still lifts the box.
    apply_dual_text_overlay (narration + subtitle together) no longer goes through here.
    """
    position = "bottom"
    try:
        from PIL import Image, ImageDraw
        import json as _json

        # normalize text → str
        if isinstance(text, list):
            text = _json.dumps(text, ensure_ascii=False)
        text = clean_overlay_text(text)
        if not text:
            return True          # nothing worth drawing: never an empty box or bar

        img = Image.open(image_path).convert("RGBA")
        W, H = img.size
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font = _load_overlay_font(font_size)

        pad_x = int(W * 0.04)
        pad_y = int(H * 0.012)
        gap   = int(H * 0.008)
        max_text_w = int(W * 0.88)

        # ── multi-box (narration_box) ────────────────────────────────
        if style == "narration_box":
            boxes = _parse_dialogue_boxes(text)
            if not boxes:
                boxes = [{"type": "narration", "text": text.strip()}]

            measured = []
            for b in boxes:
                btype = str(b.get("type", "narration")).lower()
                btext = str(b.get("text", "")).strip()
                bchar = str(b.get("character", "")).strip()
                if not btext:
                    continue
                display = f"{bchar}: {btext}" if (btype == "speech" and bchar) else btext
                lines = _wrap_text(display, font, max_text_w, draw)
                lw, lh = [], []
                for ln in lines:
                    bb = draw.textbbox((0, 0), ln, font=font)
                    lw.append(bb[2] - bb[0]); lh.append(bb[3] - bb[1])
                lg = max(2, int(font_size * 0.25))
                block_h = sum(lh) + lg * (len(lines) - 1)
                measured.append({
                    "lines": lines, "type": btype, "line_w": lw, "line_h": lh,
                    "line_gap": lg, "tw": max(lw) if lw else 0, "th": block_h,
                    "bh": block_h + pad_y * 2,
                })
            if not measured:
                return False

            total_h = sum(m["bh"] for m in measured) + gap * (len(measured) - 1)
            cur_y = pad_y * 2 if position == "top" else H - total_h - pad_y * 2 - y_offset

            for m in measured:
                box_w = min(m["tw"] + pad_x * 2, int(W * 0.94))
                bx = (W - box_w) // 2
                by, bh = cur_y, m["bh"]
                if m["type"] == "speech":
                    fill, outline, tfill = (255, 255, 255, 215), (20, 20, 20, 220), (20, 20, 20, 255)
                else:
                    fill, outline, tfill = (255, 248, 220, 225), (40, 30, 10, 200), (30, 20, 10, 255)
                draw.rounded_rectangle([bx, by, bx + box_w, by + bh],
                                       radius=10, fill=fill, outline=outline, width=2)
                ty = by + pad_y
                for i, ln in enumerate(m["lines"]):
                    tx = bx + (box_w - m["line_w"][i]) // 2
                    draw.text((tx, ty), ln, font=font, fill=tfill)
                    ty += m["line_h"][i] + m["line_gap"]
                cur_y += bh + gap

            Image.alpha_composite(img, overlay).convert("RGB").save(image_path, quality=95)
            return True

        # ── narration_box_pil: compact card placed by image analysis ─
        if style == "narration_box_pil":
            subjects = _safe_detect_subjects(image_path)          # file is still the pristine render
            pl = _place_narration(img, text.strip(), font_size, draw, W, H, y_offset, subjects)
            if pl is None:
                return False
            _draw_narration_card(draw, pl)
            Image.alpha_composite(img, overlay).convert("RGB").save(image_path, quality=95)
            return True

        # ── subtitle_bar: fixed bottom bar (also the default for any other non-caption style) ──
        if style != "caption_box":
            _draw_subtitle_bar(draw, W, H, _subtitle_bar_metrics(draw, text, W, H, font_size), y_offset)
            Image.alpha_composite(img, overlay).convert("RGB").save(image_path, quality=95)
            return True

        # ── single box / bar ─────────────────────────────────────────
        lines = _wrap_text(text.strip(), font, max_text_w, draw)
        lw, lh = [], []
        for ln in lines:
            bb = draw.textbbox((0, 0), ln, font=font)
            lw.append(bb[2] - bb[0]); lh.append(bb[3] - bb[1])
        lg = max(2, int(font_size * 0.25))
        text_h = sum(lh) + lg * (len(lines) - 1)
        text_w = max(lw) if lw else 0
        box_h = text_h + pad_y * 2

        if style == "caption_box":
            box_w = min(text_w + pad_x * 2, int(W * 0.94))
            bx = (W - box_w) // 2
            by = pad_y * 2 if position == "top" else H - box_h - pad_y * 2 - y_offset
            draw.rounded_rectangle([bx, by, bx + box_w, by + box_h],
                                   radius=12, fill=(0, 0, 0, 175),
                                   outline=(255, 255, 255, 130), width=2)
            ty = by + pad_y
            for i, ln in enumerate(lines):
                tx = bx + (box_w - lw[i]) // 2
                draw.text((tx + 1, ty + 1), ln, font=font, fill=(0, 0, 0, 200))
                draw.text((tx, ty), ln, font=font, fill=(255, 255, 255, 255))
                ty += lh[i] + lg
        Image.alpha_composite(img, overlay).convert("RGB").save(image_path, quality=95)
        return True

    except Exception as e:
        print(f"[dialogue overlay] {e}")
        return False
