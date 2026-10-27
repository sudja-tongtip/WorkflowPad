"""
Image Editor v2 – Working Folder workspace (edit history lives in the folder), zoom/pan viewer
"""
import asyncio, base64, json, os, random, shutil, sys, time, urllib.parse, urllib.request, uuid
from pathlib import Path
import flet as ft
import update_check as _update_check
import app_error as _app_error
import export_util as _export_util
try:
    import flet_code_editor as fce
    _HAS_FCE = True
except ImportError:
    _HAS_FCE = False

# ── App metadata ──────────────────────
APP_ID      = "image_editor"
APP_NAME    = "Image Editor"
APP_VERSION = "0.2.0"   # SemVer; see RELEASE.md

# ── Config ────────────────────────────────────────────────────────────
COMFY_HOST  = "127.0.0.1:8188"
# Folder with comfy_apis/ (image_create, image_edit, llm, caption, enhance), icons/: the project folder, or (PyInstaller) the folder next to the EXE.
APP_ROOT    = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
API_DIR     = APP_ROOT / "comfy_apis" / "image_edit"      # edit workflows (they need a source image)
CREATE_DIR  = APP_ROOT / "comfy_apis" / "image_create"    # text-to-image workflows (the New image dialog)


def _wf_file(api_fn: str) -> Path:
    """A workflow by name: the edit folder first, then the create folder (a picture made with New is processed again with the same file)."""
    for d in (API_DIR, CREATE_DIR):
        if (d / api_fn).is_file():
            return d / api_fn
    return API_DIR / api_fn
CAPTION_DIR = APP_ROOT / "comfy_apis" / "caption"      # image -> prompt workflows (JoyCaption, Florence2)
CAPTION_DIR.mkdir(parents=True, exist_ok=True)

# App data — config in APPDATA, large data in LOCALAPPDATA
APP_CONFIG        = Path(os.environ.get("APPDATA",   str(Path.home()))) / "WorkflowPad"
APP_DATA          = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WorkflowPad"
TEMP_DIR          = APP_DATA   / "temp"          # ephemeral scratch (argument mode, intermediate processing)
PROMPT_HIST_PATH  = APP_CONFIG / "saved_prompt_history.json"
EDITOR_CONFIG_PATH = APP_CONFIG / "image_editor_config.json"   # global app settings only (last / recent folders)
# LEGACY (previous builds): per-project data. These are no longer used or created, and are never deleted here,
# so old projects stay on disk untouched.
PROJECTS_DIR      = APP_DATA   / "projects"
PROJECT_LIST_PATH = APP_CONFIG / "project_list.json"

for _d in (APP_CONFIG, APP_DATA, TEMP_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# mutable: where generated results are written. Working Folder open -> <folder>/.image_editor/versions
# (persistent history versions); otherwise the ephemeral TEMP_DIR (argument mode).
_cur_temp: list = [TEMP_DIR]
def get_temp_dir() -> Path: return _cur_temp[0]

# ── Palette ───────────────────────────────────────────────────────────
C = {
    "bg"      : "#F3F4F6",
    "surface" : "#FFFFFF",
    "panel"   : "#F9FAFB",
    "panel2"  : "#F0F0F0",
    "border"  : "#D1D5DB",
    "accent"  : "#2563EB",
    "green"   : "#16A34A",
    "red"     : "#DC2626",
    "amber"   : "#D97706",
    "text1"   : "#111827",
    "text2"   : "#374151",
    "text3"   : "#6B7280",
    "toolbar" : "#FFFFFF",
}

def po(left=0,top=0,right=0,bottom=0): return ft.Padding(left=left,top=top,right=right,bottom=bottom)
def br(r): return ft.BorderRadius(r,r,r,r)

# ── ComfyUI ───────────────────────────────────────────────────────────
def comfy_upload(path:str)->str:
    with open(path,"rb") as f: data=f.read()
    # Add timestamp to prevent ComfyUI from caching old images
    ext  = Path(path).suffix
    fn   = f"{Path(path).stem}_{int(time.time()*1000)}{ext}"
    bd="----B"+uuid.uuid4().hex
    body=(f"--{bd}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{fn}\"\r\nContent-Type: image/jpeg\r\n\r\n").encode()+data+f"\r\n--{bd}--\r\n".encode()
    req=urllib.request.Request(f"http://{COMFY_HOST}/upload/image",data=body,headers={"Content-Type":f"multipart/form-data; boundary={bd}"},method="POST")
    with urllib.request.urlopen(req,timeout=30) as r: return json.loads(r.read())["name"]

def comfy_queue(wf:dict,cid:str)->str:
    # Filter only nodes that are real dicts
    clean_wf = {k:v for k,v in wf.items() if isinstance(v,dict) and "class_type" in v}
    req=urllib.request.Request(f"http://{COMFY_HOST}/prompt",data=json.dumps({"prompt":clean_wf,"client_id":cid}).encode(),headers={"Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=30) as r: return json.loads(r.read())["prompt_id"]
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"[COMFY ERROR] HTTP {e.code}: {body[:500]}")
        raise

def comfy_hist(pid:str)->dict:
    with urllib.request.urlopen(f"http://{COMFY_HOST}/history/{pid}",timeout=10) as r: return json.loads(r.read())

def comfy_cancel(pid:str)->None:
    """Cancel a job: remove it from the queue and, if it is the one running, interrupt it."""
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://{COMFY_HOST}/queue",
            data=json.dumps({"delete":[pid]}).encode(),headers={"Content-Type":"application/json"},method="POST"),timeout=5)
    except Exception: pass
    running=None
    try:
        with urllib.request.urlopen(f"http://{COMFY_HOST}/queue",timeout=5) as r:
            running=[it[1] for it in json.loads(r.read()).get("queue_running",[]) if len(it)>1]
    except Exception: pass
    if running is None or pid in running:   # never interrupt somebody else's job
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://{COMFY_HOST}/interrupt",
                data=b"",headers={"Content-Type":"application/json"},method="POST"),timeout=5)
        except Exception: pass

class GenerationStopped(Exception):
    """Raised by the polling loops when the user pressed Stop; callers show it like any other message."""

_prog_stop = asyncio.Event()      # set by the Stop button, cleared when the next operation starts
_ACTIVE_PIDS: set = set()         # ComfyUI jobs being waited on (so Stop can cancel them at once)

def comfy_get_img(fn,sub,ft_)->bytes:
    p=urllib.parse.urlencode({"filename":fn,"subfolder":sub,"type":ft_})
    with urllib.request.urlopen(f"http://{COMFY_HOST}/view?{p}",timeout=30) as r: return r.read()

# LoadImage only — excludes LoadImageMask (mask node is separate)
_IMG_CLS=["LoadImage","ETN_LoadImageBase64","ImageBatch","LoadImageFromUrl"]
_MASK_INPUT_CLS=["LoadImageMask","ImageToMask","MaskFromImage"]
_PCLS=["CLIPTextEncodeFlux","CLIPTextEncodeSD3","CLIPTextEncodeHunyuan","smZ CLIPTextEncode","BNK_CLIPTextEncodeAdvanced","CLIPTextEncode"]
_SCLS=["RandomNoise","KSamplerAdvanced","KSampler","SamplerCustomAdvanced","SamplerCustom"]
_SF=["noise_seed","seed","rand_seed"]
_PH={"positive","pos","prompt","clip positive","positive prompt"}
_LATENT_CLS=["EmptyLatentImage","EmptySD3LatentImage","EmptyHunyuanLatentVideo",
             "EmptyLatentImagePresets","EmptyFlux2LatentImage"]

# Fixed sizes per ratio (multiple of 64, S/M/L/XL) — matches prompt_tester.py
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

def detect(wf:dict)->dict:
    in_=if_=sn=sf=None; cands=[]
    for nid,node in wf.items():
        if not isinstance(node,dict): continue
        ct=node.get("class_type",""); inp=node.get("inputs",{})
        if ct in _IMG_CLS:
            if "image" in inp: in_,if_=nid,"image"
            elif not in_:
                for k,v in inp.items():
                    if not isinstance(v,list): in_,if_=nid,k; break
        pr=next((i for i,c in enumerate(_PCLS) if c==ct),None)
        if pr is None and "clip" in ct.lower() and "encode" in ct.lower(): pr=len(_PCLS)
        if pr is None:
            for f in ("text","prompt","positive","negative"):
                if f in inp and isinstance(inp[f],str): pr=len(_PCLS)+10; break
        if pr is not None:
            tf=next((f for f in ("text","prompt","positive","negative") if f in inp and isinstance(inp[f],str)),None)
            if tf is None: tf=next((k for k,v in inp.items() if isinstance(v,str) and v),"text")
            t=(wf.get(nid,{}).get("_meta",{}).get("title","") or "").lower()
            role="negative" if any(h in t for h in ("neg","negative")) else ("positive" if any(h in t for h in _PH) else "unknown")
            cands.append((pr,nid,tf,role))
    cands.sort(key=lambda x:(x[0],0 if x[3]=="positive" else 1))
    pn=[]; seen=False
    for p,nid,tf,role in cands:
        if role=="unknown" and not seen: role="positive"
        if role=="positive": seen=True
        pn.append({"node":nid,"field":tf,"role":role})
    for cls in _SCLS:
        for nid,node in wf.items():
            if not isinstance(node,dict): continue
            if node.get("class_type","")==cls:
                for s in _SF:
                    if s in node.get("inputs",{}): sn,sf=nid,s; break
                if sn: break
        if sn: break
    if not sn:
        for nid,node in wf.items():
            if not isinstance(node,dict): continue
            for s in _SF:
                if s in node.get("inputs",{}) and isinstance(node["inputs"][s],(int,float)): sn,sf=nid,s; break
            if sn: break
    return {"in_node":in_,"in_field":if_ or "image","prompt_nodes":pn,"seed_node":sn,"seed_field":sf or "noise_seed","needs_img":in_ is not None}

def _detect_mask_channel(mask_path:str)->str:
    """
    Check whether the mask file has a real alpha channel with data.
    - RGBA + alpha has data (min != max) → use "alpha"  (both app-generated masks and clipspace)
    - RGB only → use "red" (fallback)
    """
    try:
        from PIL import Image
        im = Image.open(mask_path)
        if "A" in im.getbands():
            alpha = im.getchannel("A")
            mn, mx = alpha.getextrema()
            if mn != mx:
                return "alpha"
    except Exception as e:
        print(f"  ⚠ _detect_mask_channel error: {e}")
    return "red"

def resolve_wildcards(text: str) -> str:
    """Convert {a|b|c} → pick one at random; supports nesting"""
    import re, random
    for _ in range(20):  # max 20 passes for nested wildcards
        m = re.search(r'\{([^{}]+)\}', text)
        if not m: break
        choices = m.group(1).split('|')
        text = text[:m.start()] + random.choice(choices) + text[m.end():]
    return text

def inject_size(wf: dict, width: int, height: int) -> dict:
    """Inject width/height into every EmptyLatentImage-style node in the workflow"""
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        ct  = node.get("class_type", "")
        inp = node.get("inputs", {})
        if ct in _LATENT_CLS:
            if "width"  in inp: wf[nid]["inputs"]["width"]  = width
            if "height" in inp: wf[nid]["inputs"]["height"] = height
    return wf

_STEP_SAMPLERS=("KSampler","KSamplerAdvanced","SamplerCustom","SamplerCustomAdvanced")
_STEPS_MAX=10000

def _is_link(v)->bool:
    return isinstance(v,list) and len(v)==2 and isinstance(v[1],int)

def _literal_int(v)->bool:
    return isinstance(v,int) and not isinstance(v,bool)

def _steps_from_node(wf:dict,nid,field:str,out:set,seen:set,depth:int=0):
    """Resolve where the node's `field` (an int steps value) really lives: a literal here, or a primitive it links to."""
    node=wf.get(nid)
    if not isinstance(node,dict): return
    v=node.get("inputs",{}).get(field)
    if _literal_int(v): out.add((nid,field)); return
    if _is_link(v) and depth<4:
        src=str(v[0]); sn=wf.get(src)
        if isinstance(sn,dict) and _literal_int(sn.get("inputs",{}).get("value")):
            out.add((src,"value"))

def _walk_sigmas(wf:dict,nid,out:set,seen:set,depth:int=0):
    """Follow a `sigmas` link upstream to the scheduler node(s) that own `steps`."""
    nid=str(nid)
    if nid in seen or depth>8: return
    seen.add(nid)
    node=wf.get(nid)
    if not isinstance(node,dict): return
    inp=node.get("inputs",{})
    if "steps" in inp: _steps_from_node(wf,nid,"steps",out,seen)
    for k,v in inp.items():
        if k=="sigmas" and _is_link(v): _walk_sigmas(wf,v[0],out,seen,depth+1)

def find_steps_target(wf:dict)->dict:
    """Locate the one editable steps field that drives generation.
    Returns {"target":(node_id,field)|None, "value":int|None, "reason":str}. Never guesses between several."""
    cands=set()
    for nid,node in wf.items():
        if not isinstance(node,dict) or node.get("class_type") not in _STEP_SAMPLERS: continue
        inp=node.get("inputs",{})
        if "steps" in inp: _steps_from_node(wf,nid,"steps",cands,set())
        if _is_link(inp.get("sigmas")): _walk_sigmas(wf,inp["sigmas"][0],cands,set())
    if not cands: return {"target":None,"value":None,"reason":"No editable steps in this workflow"}
    if len(cands)>1: return {"target":None,"value":None,"reason":f"{len(cands)} steps fields found – ambiguous, using workflow values"}
    nid,field=next(iter(cands))
    return {"target":(nid,field),"value":wf[nid]["inputs"][field],"reason":""}

def read_workflow_steps(api_fn:str)->dict:
    try:
        with open(_wf_file(api_fn),encoding="utf-8") as f: return find_steps_target(json.load(f))
    except Exception as ex:
        return {"target":None,"value":None,"reason":f"Cannot read workflow ({ex})"}

def inject_steps(wf:dict,steps:int)->bool:
    t=find_steps_target(wf)["target"]
    if not t or not _literal_int(steps) or not 1<=steps<=_STEPS_MAX: return False
    wf[t[0]]["inputs"][t[1]]=steps
    return True

def build_wf(api_fn:str,prompt:str,img_path:str|None=None,
             mask_path:str|None=None,mask_local_path:str|None=None,
             width:int|None=None,height:int|None=None,steps:int|None=None)->dict:
    with open(_wf_file(api_fn),encoding="utf-8") as f: wf=json.load(f)
    m=detect(wf)

    print(f"\n[DEBUG] build_wf: {api_fn}")
    print(f"  image_node: {m['in_node']} field: {m['in_field']}")
    print(f"  img_path:   {img_path}")
    print(f"  mask_path:  {mask_path}")

    # Inject image → LoadImage node
    if m["in_node"] and img_path:
        wf[m["in_node"]]["inputs"][m["in_field"]] = img_path
        print(f"  ✓ injected image → node {m['in_node']}")

    # Inject mask → LoadImageMask node
    if mask_path:
        found_mask = False
        for nid,node in wf.items():
            if not isinstance(node,dict): continue
            ct = node.get("class_type","")
            if ct in _MASK_INPUT_CLS:
                inp = node.get("inputs",{})
                # Try "image" field first (input that stores the filename)
                if "image" in inp:
                    wf[nid]["inputs"]["image"] = mask_path
                    print(f"  ✓ injected mask → node {nid} [{ct}] field [image]")
                    # App-generated masks always store data in the alpha channel
                    if "channel" in inp:
                        wf[nid]["inputs"]["channel"] = "alpha"
                        print(f"  ✓ set channel → alpha")
                    found_mask = True
                    break
                else:
                    # If no "image" field, find the first string field
                    for k,v in inp.items():
                        if isinstance(v,str) and not isinstance(v,list):
                            wf[nid]["inputs"][k] = mask_path
                            print(f"  ✓ injected mask → node {nid} [{ct}] field [{k}]")
                            found_mask = True
                            break
                if found_mask: break
        if not found_mask:
            print(f"  ✗ WARNING: No mask node found in workflow!")
            # Show all nodes for debugging
            for nid,node in wf.items():
                if isinstance(node,dict):
                    print(f"    node {nid}: {node.get('class_type','?')}")

    for ph in m["prompt_nodes"]:
        if ph["role"] in ("positive","unknown"):
            resolved = resolve_wildcards(prompt)
            if resolved != prompt:
                print(f"  ✓ wildcard resolved: {prompt!r} → {resolved!r}")
            wf[ph["node"]]["inputs"][ph["field"]]=resolved
            print(f"  ✓ injected prompt → node {ph['node']} [{ph['field']}]")
            break
    if m["seed_node"]:
        wf[m["seed_node"]]["inputs"][m["seed_field"]]=random.randint(0,2**53)
        print(f"  ✓ randomized seed → node {m['seed_node']}")

    if width and height:
        wf = inject_size(wf, width, height)
        print(f"  ✓ injected size → {width}×{height}")

    if steps is not None:
        print(f"  ✓ injected steps → {steps}" if inject_steps(wf,steps) else "  ✗ steps not injected (no unambiguous target)")

    return wf

def scan_apis()->list[str]:
    return sorted(p.name for p in API_DIR.glob("*.json") if "project" not in p.name and "editor" not in p.name)

def auto_extend_plan(iw: int, ih: int, left: int, right: int, top: int, bottom: int, feather: int = 0, block: int = 8) -> dict | None:
    """Extend 'Auto' resize: scale the picture and the four extensions together so that the EXTENDED canvas has the size of the original picture (rounded to a multiple of `block` = 8, the
    step ComfyUI rounds to). Meant for a locked aspect ratio, where both axes need almost the same factor; each axis uses its own factor so the result is exactly the target size.
    Returns None when there is nothing to extend. scale is the share of the original the picture is reduced to (<= 1)."""
    if left + right + top + bottom <= 0 or iw <= 0 or ih <= 0:
        return None
    tw = max(block, round(iw / block) * block)
    th = max(block, round(ih / block) * block)
    sx = min(1.0, tw / (iw + left + right))
    sy = min(1.0, th / (ih + top + bottom))
    pl, pr = int(round(left * sx)), int(round(right * sx))
    pt, pb = int(round(top * sy)), int(round(bottom * sy))
    sw, sh = max(64, tw - pl - pr), max(64, th - pt - pb)
    return {"out_w": sw + pl + pr, "out_h": sh + pt + pb, "src_w": sw, "src_h": sh, "left": pl, "right": pr, "top": pt, "bottom": pb,
            "feather": int(round(feather * (sx + sy) / 2)), "scale": min(sw / iw, sh / ih)}

def _extend_log(msg: str) -> None:
    """Console + a small log file (%LOCALAPPDATA%\\WorkflowPad\\debug\\extend_log.txt) so a surprising Extend result can be checked against what was really sent."""
    print(msg)
    try:
        d = APP_DATA / "debug"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "extend_log.txt", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except Exception:
        pass

def scan_create_apis()->list[str]:
    """Text-to-image workflows (comfy_apis/image_create) for the New image dialog."""
    return sorted(p.name for p in CREATE_DIR.glob("*.json") if "project" not in p.name and "editor" not in p.name)

_MASK_NODE_CLASSES = ["LoadImageMask", "ImageToMask", "MaskFromImage",
                       "InpaintModelConditioning", "VAEEncodeForInpaint"]

ENHANCE_DIR = APP_ROOT / "comfy_apis" / "enhance"
ENHANCE_DIR.mkdir(parents=True, exist_ok=True)

def scan_enhance_apis() -> list[str]:
    return sorted(p.name for p in ENHANCE_DIR.glob("*.json"))

def _enhance_nodes_of(name: str) -> set:
    try:
        wf = json.loads((ENHANCE_DIR / name).read_text(encoding="utf-8"))
        return {n.get("class_type") for n in wf.values() if isinstance(n, dict)}
    except Exception:
        return set()

def scan_face_upscale_apis() -> list[str]:
    """Workflows in comfy_apis/enhance that run_enhance can drive: they contain a Face Restore and/or an Upscale node (an outpaint workflow, for example, does not)."""
    return [p.name for p in sorted(ENHANCE_DIR.glob("*.json")) if _enhance_nodes_of(p.name) & {"FaceRestoreCFWithModel", "ImageUpscaleWithModel"}]

def scan_outpaint_apis() -> list[str]:
    """Workflows in comfy_apis/enhance that can extend a picture: they contain a Pad Image for Outpainting node (run_outpaint sets its four sides)."""
    found = []
    for p in sorted(ENHANCE_DIR.glob("*.json")):
        try:
            wf = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(wf, dict) and any(isinstance(n, dict) and n.get("class_type") == "ImagePadForOutpaint" for n in wf.values()):
                found.append(p.name)
        except Exception:
            continue
    return found

async def _run_wf_and_save(wf: dict, on_progress=None) -> str | None:
    """Queue workflow and wait for result via polling (no websocket)"""
    cid = str(uuid.uuid4())
    loop = asyncio.get_event_loop()
    out_dir = get_temp_dir()                     # fixed now: a result never lands in a folder opened later
    pid = await loop.run_in_executor(None, comfy_queue, wf, cid)
    _ACTIVE_PIDS.add(pid)
    try:
        tick = 0; deadline = time.time() + 600
        while time.time() < deadline:
            if _prog_stop.is_set():
                await loop.run_in_executor(None, comfy_cancel, pid)
                raise GenerationStopped("Stopped by user")
            await asyncio.sleep(2); tick += 1
            if on_progress: on_progress(f"🔄 {tick*2}s")
            hist = await loop.run_in_executor(None, comfy_hist, pid)
            if pid in hist:
                for ndata in hist[pid].get("outputs", {}).values():
                    for img in ndata.get("images", []):
                        raw = await loop.run_in_executor(None, comfy_get_img,
                            img["filename"], img.get("subfolder",""), img.get("type","output"))
                        dst = out_dir / f"v_{uuid.uuid4().hex[:8]}.png"
                        dst.write_bytes(raw)
                        return str(dst)
                break
    finally:
        _ACTIVE_PIDS.discard(pid)
    return None

async def run_image2prompt(api_fn: str, img_path: str) -> str | None:
    """Send image to Florence2 and read the prompt from the file written by SaveText"""
    with open(CAPTION_DIR/api_fn, encoding="utf-8") as f: wf = json.load(f)
    loop = asyncio.get_event_loop()

    # Find SaveText node to know the output file path
    save_root = "output"; save_file = "file.txt"
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        if "SaveText" in node.get("class_type", ""):
            save_root = node["inputs"].get("root_dir", "output")
            save_file = node["inputs"].get("file", "file.txt")
            break

    # Watch the SaveText file only when the workflow gives an absolute root.
    # A relative root is resolved by ComfyUI itself, so we read the result from
    # the queue history instead of guessing where ComfyUI is installed.
    possible_paths = []
    _save_root = Path(save_root)
    if _save_root.is_absolute():
        possible_paths.append(_save_root / save_file)

    # inject image
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        if node.get("class_type") == "LoadImage":
            uploaded = await loop.run_in_executor(None, comfy_upload, img_path)
            node["inputs"]["image"] = uploaded
            break

    # Remove text_0 that may break Impact Pack
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        if "ShowText" in node.get("class_type","") and "text_0" in node.get("inputs",{}):
            del node["inputs"]["text_0"]

    # Record timestamp before queuing to detect new files
    mtime_before = {}
    for p in possible_paths:
        if p.exists(): mtime_before[str(p)] = p.stat().st_mtime

    cid = str(uuid.uuid4())
    pid = await loop.run_in_executor(None, comfy_queue, wf, cid)
    print(f"[IMG2PROMPT] queued pid={pid} watching {save_root}/{save_file}")

    # Poll for file change or history
    _ACTIVE_PIDS.add(pid)
    try:
        deadline = time.time() + 120
        while time.time() < deadline:
            if _prog_stop.is_set():
                await loop.run_in_executor(None, comfy_cancel, pid)
                raise GenerationStopped("Stopped by user")
            await asyncio.sleep(2)

            # Check file first
            for p in possible_paths:
                if p.exists():
                    mtime = p.stat().st_mtime
                    if str(p) not in mtime_before or mtime > mtime_before.get(str(p), 0):
                        text = p.read_text(encoding="utf-8").strip()
                        if text:
                            # Read last line added (append mode)
                            lines = [l.strip() for l in text.split("\n") if l.strip()]
                            if lines:
                                print(f"[IMG2PROMPT] got text from {p}")
                                return lines[-1]

            # Fallback: check history
            hist = await loop.run_in_executor(None, comfy_hist, pid)
            if pid in hist:
                outputs = hist[pid].get("outputs", {})
                for ndata in outputs.values():
                    for key in ("text", "string", "texts"):
                        val = ndata.get(key)
                        if val:
                            result = val[0] if isinstance(val, list) else str(val)
                            print(f"[IMG2PROMPT] got text from history key={key}")
                            return result
                if outputs: break

    finally:
        _ACTIVE_PIDS.discard(pid)
    return None

async def run_outpaint(api_fn, img_path, prompt, left=0, top=0, right=0, bottom=0, feathering=150, on_progress=None):
    """Outpaint image — expand canvas in specified directions"""
    with open(ENHANCE_DIR/api_fn, encoding="utf-8") as f: wf = json.load(f)
    loop = asyncio.get_event_loop()

    # inject image
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        if node.get("class_type") == "LoadImage":
            uploaded = await loop.run_in_executor(None, comfy_upload, img_path)
            node["inputs"]["image"] = uploaded; break

    # inject pad values
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        if node.get("class_type") == "ImagePadForOutpaint":
            node["inputs"]["left"] = left
            node["inputs"]["top"] = top
            node["inputs"]["right"] = right
            node["inputs"]["bottom"] = bottom
            node["inputs"]["feathering"] = feathering; break

    # inject prompt
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        ct = node.get("class_type","")
        t = (node.get("_meta",{}).get("title","") or "").lower()
        if ct == "CLIPTextEncode" and "negative" not in t and "zero" not in t:
            node["inputs"]["text"] = prompt; break

    # randomize seed
    for nid, node in wf.items():
        if not isinstance(node, dict): continue
        if "seed" in node.get("inputs", {}):
            node["inputs"]["seed"] = int(uuid.uuid4().int % (2**32)); break

    # log what is really sent (the uploaded file's own pixel size, not what the dialog planned)
    try:
        from PIL import Image as _PI
        with _PI.open(img_path) as _im:
            sw, sh = _im.size
        tw, th = sw + left + right, sh + top + bottom
        _msg = (f"[EXTEND] SENT workflow={api_fn} | source {sw}x{sh} ({Path(img_path).name}) + pads L{left} R{right} T{top} B{bottom} feather {feathering}"
                f" -> canvas {tw}x{th} = {tw*th/1e6:.2f} MP" + ("" if tw % 8 == 0 and th % 8 == 0 else " (not a multiple of 8: ComfyUI rounds it)"))
        _extend_log(_msg)
        if on_progress:
            try: on_progress(f"⏳ Extend: sending {tw}×{th} ({tw*th/1e6:.2f} MP)...")
            except Exception: pass
    except Exception as _ex:
        _extend_log(f"[EXTEND] could not log the sent size: {_ex}")

    return await _run_wf_and_save(wf, on_progress)

async def run_enhance(api_fn,img_path,restore_face=True,upscale=True,on_progress=None):
    with open(ENHANCE_DIR/api_fn,encoding="utf-8") as f: wf=json.load(f)
    loop = asyncio.get_event_loop()

    # upload image → LoadImage node
    for nid,node in wf.items():
        if not isinstance(node,dict): continue
        if node.get("class_type")=="LoadImage":
            uploaded=await loop.run_in_executor(None,comfy_upload,img_path)
            node["inputs"]["image"]=uploaded
            load_nid = nid; break

    # Find node IDs by class_type
    face_restore_nid = next((n for n,v in wf.items() if isinstance(v,dict) and v.get("class_type")=="FaceRestoreCFWithModel"), None)
    upscale_nid      = next((n for n,v in wf.items() if isinstance(v,dict) and v.get("class_type")=="ImageUpscaleWithModel"), None)
    save_nid         = next((n for n,v in wf.items() if isinstance(v,dict) and v.get("class_type")=="SaveImage"), None)
    face_loader_nid  = next((n for n,v in wf.items() if isinstance(v,dict) and v.get("class_type")=="FaceRestoreModelLoader"), None)
    up_loader_nid    = next((n for n,v in wf.items() if isinstance(v,dict) and v.get("class_type")=="UpscaleModelLoader"), None)

    # Set image source for each step
    # flow: LoadImage → [FaceRestore] → [Upscale] → SaveImage
    img_after_face   = [face_restore_nid, 0] if restore_face else [load_nid, 0]
    img_for_save     = [upscale_nid, 0] if upscale else img_after_face

    if restore_face and face_restore_nid:
        wf[face_restore_nid]["inputs"]["image"] = [load_nid, 0]
    elif not restore_face:
        # Remove face restore nodes
        for nid in [face_restore_nid, face_loader_nid]:
            if nid and nid in wf: del wf[nid]

    if upscale and upscale_nid:
        wf[upscale_nid]["inputs"]["image"] = img_after_face
    elif not upscale:
        # Remove upscale nodes
        for nid in [upscale_nid, up_loader_nid]:
            if nid and nid in wf: del wf[nid]

    if save_nid:
        wf[save_nid]["inputs"]["images"] = img_for_save

    print(f"[ENHANCE] restore={restore_face} upscale={upscale} → save from {img_for_save}")
    return await _run_wf_and_save(wf, on_progress)

def scan_mask_apis() -> list[str]:
    """Return list of workflows that contain a mask-related node"""
    result = []
    for p in sorted(API_DIR.glob("*.json")):
        if "project" in p.name or "editor" in p.name:
            continue
        try:
            wf = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(wf, dict): continue
            for node in wf.values():
                if not isinstance(node, dict): continue
                if node.get("class_type","") in _MASK_NODE_CLASSES:
                    result.append(p.name)
                    break
        except Exception:
            pass
    return result

async def run_comfy(api_fn:str,prompt:str,img_path:str|None=None,
                    mask_path:str|None=None,on_progress=None,
                    width:int|None=None,height:int|None=None,steps:int|None=None)->str|None:
    loop=asyncio.get_event_loop(); cid=uuid.uuid4().hex
    out_dir=get_temp_dir()                       # fixed now: a result never lands in a folder opened later
    uploaded=None; uploaded_mask=None
    if img_path: uploaded=await loop.run_in_executor(None,comfy_upload,img_path)
    if mask_path: uploaded_mask=await loop.run_in_executor(None,comfy_upload,mask_path)
    print(f"[DEBUG] uploaded image name: {uploaded}")
    print(f"[DEBUG] uploaded mask name:  {uploaded_mask}")
    wf=build_wf(api_fn,prompt,uploaded,uploaded_mask,mask_local_path=mask_path,
                width=width,height=height,steps=steps)
    pid=await loop.run_in_executor(None,comfy_queue,wf,cid)
    _ACTIVE_PIDS.add(pid)
    try:
        tick=0; deadline=time.time()+300
        while time.time()<deadline:
            if _prog_stop.is_set():
                await loop.run_in_executor(None, comfy_cancel, pid)
                raise GenerationStopped("Stopped by user")
            await asyncio.sleep(2); tick+=1
            if on_progress: on_progress(f"🔄 {tick*2}s")
            hist=await loop.run_in_executor(None,comfy_hist,pid)
            if pid in hist:
                for out in hist[pid].get("outputs",{}).values():
                    if "images" in out:
                        info=out["images"][0]
                        img=await loop.run_in_executor(None,comfy_get_img,info["filename"],info.get("subfolder",""),info.get("type","output"))
                        dst=out_dir/f"v_{uuid.uuid4().hex[:8]}.jpg"
                        dst.write_bytes(img)
                        return str(dst)
                break
    finally:
        _ACTIVE_PIDS.discard(pid)
    return None

def _b64(path:str)->str:
    try:
        data=Path(path).read_bytes(); ext=Path(path).suffix.lower().lstrip(".")
        mime={"jpg":"jpeg","jpeg":"jpeg","png":"png","webp":"webp","bmp":"bmp"}.get(ext,"jpeg")
        return f"data:image/{mime};base64,"+base64.b64encode(data).decode()
    except: return path

def _thumb(path:str,size:int=160)->str:
    try:
        from PIL import Image; import io
        img=Image.open(path); img.thumbnail((size,size))
        if img.mode not in ("RGB","RGBA"): img=img.convert("RGB")
        buf=io.BytesIO(); img.save(buf,format="JPEG",quality=75)
        return "data:image/jpeg;base64,"+base64.b64encode(buf.getvalue()).decode()
    except: return _b64(path)

class _Zoomer:
    """Zoom + pan of a picture with a transform the app fully owns.
    `xf` is a Container that fills the viewport and holds the picture; it is scaled about its top-left corner and shifted with
    Container.offset (verified in the browser: the offset is applied in the parent's pixels, outside the scale). State is
    (scale, tx, ty): screen = t + scale * content. The anchor of a zoom stays under the cursor (wheel) or at the viewport centre
    (buttons / keys), the picture can never be dragged out of the viewport, and scale 1.0 == "Fit".
    (flet's InteractiveViewer.zoom() scales about the top-left corner and its wheel zoom reports nothing back, so it can not
    keep a reliable zoom state.)"""
    ZMIN, ZMAX = 1.0, 10.0

    def __init__(self, xf, on_change=None):
        self.xf, self.on_change = xf, on_change
        self.scale, self.tx, self.ty, self.vw, self.vh = 1.0, 0.0, 0.0, 0.0, 0.0

    def _clamp(self):
        self.tx = min(0.0, max(self.vw * (1 - self.scale), self.tx)) if self.vw > 0 else 0.0
        self.ty = min(0.0, max(self.vh * (1 - self.scale), self.ty)) if self.vh > 0 else 0.0

    def _apply(self):
        self._clamp()
        self.xf.scale = ft.Scale(scale=self.scale, alignment=ft.Alignment(-1, -1))
        self.xf.offset = ft.Offset(self.tx / self.vw if self.vw > 0 else 0, self.ty / self.vh if self.vh > 0 else 0)
        try: self.xf.update()
        except Exception: pass                      # not mounted yet (startup) — applied on the next change
        if self.on_change: self.on_change()

    def set_viewport(self, w, h):
        self.vw, self.vh = float(w or 0), float(h or 0)
        self._apply()

    def zoom_at(self, factor, px=None, py=None):
        new = max(self.ZMIN, min(self.ZMAX, self.scale * factor))
        if abs(new - self.scale) < 1e-9: return
        if px is None or py is None: px, py = self.vw / 2, self.vh / 2
        qx, qy = (px - self.tx) / self.scale, (py - self.ty) / self.scale        # content point under the anchor
        self.scale = new
        self.tx, self.ty = px - new * qx, py - new * qy
        self._apply()

    def pan_by(self, dx, dy):
        if self.scale <= self.ZMIN + 1e-9: return
        self.tx += dx; self.ty += dy
        self._apply()

    def reset(self):
        self.scale, self.tx, self.ty = 1.0, 0.0, 0.0
        self._apply()


def _range_ids(order: list, a, b) -> list:
    """Ids from a to b inclusive in display order (either direction); [b] when the anchor is unknown, [] when b is."""
    if b not in order: return []
    if a not in order: return [b]
    i, j = order.index(a), order.index(b)
    return order[min(i, j):max(i, j) + 1]


def _marquee_hits(n, x0, x1, y0, y1, card_w, gap, pad_x, pad_y, card_h) -> list:
    """Card indices touched (any overlap) by a rectangle in filmstrip content coordinates.
    Card i spans x = pad_x + i*(card_w+gap) .. +card_w and y = pad_y .. pad_y+card_h."""
    if n <= 0 or y1 < pad_y or y0 > pad_y + card_h: return []
    step = card_w + gap
    lo = max(0, int((x0 - pad_x) // step))
    hi = min(n - 1, int((x1 - pad_x) // step))
    return [i for i in range(lo, hi + 1) if x1 >= pad_x + i * step and x0 <= pad_x + i * step + card_w]


THUMB_CACHE:dict={}
def get_thumb(p:str)->str:
    if p not in THUMB_CACHE: THUMB_CACHE[p]=_thumb(p)
    return THUMB_CACHE[p]

# ── Global Prompt History (global: shared by every Working Folder, persists across sessions) ────────
# PROMPT_HIST_PATH is defined once in the Config block above (APP_CONFIG).
_HIST_KEYS = ("new", "edit", "mask")
_HIST_META_KEYS = ("api", "width", "height", "steps", "created_at")   # optional; legacy entries only have text/thumb

def _norm_entry(e):
    return {"text": e, "thumb": None} if isinstance(e, str) else e

def load_global_prompt_hist() -> dict:
    # One-time migration: earlier builds kept this file next to the source.
    _legacy = APP_ROOT / "saved_prompt_history.json"
    if not PROMPT_HIST_PATH.exists() and _legacy.exists():
        try: PROMPT_HIST_PATH.write_bytes(_legacy.read_bytes())
        except Exception: pass
    if PROMPT_HIST_PATH.exists():
        try:
            data = json.loads(PROMPT_HIST_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                # migrate old flat list → put everything in "new"
                return {"new": [_norm_entry(e) for e in data], "edit": [], "mask": []}
            if isinstance(data, dict):
                return {k: [_norm_entry(e) for e in data.get(k, [])] for k in _HIST_KEYS}
        except: pass
    return {"new": [], "edit": [], "mask": []}

def save_global_prompt_hist(hists: dict):
    PROMPT_HIST_PATH.write_text(json.dumps(hists, ensure_ascii=False, indent=2), encoding="utf-8")

# ── Working Folder workspace ─────────────────────────────────────────────
# The folder the user opens IS the workspace:
#   <folder>/photo.jpg ...            the user's images (the filesystem decides which ones exist)
#   <folder>/Imported/                copies of images imported from elsewhere
#   <folder>/.image_editor/           this app's data — never listed as images
#       workspace.json                history + lineage (relative paths only)
#       versions/                     generated results (the History nodes)
#       masks/                        mask helper files
#       cache/
# Global settings (last / recent folders) live in APP_CONFIG; prompt history stays global too.
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
WS_DIRNAME, WS_FILE = ".image_editor", "workspace.json"
VERSIONS_DIRNAME, MASKS_DIRNAME, CACHE_DIRNAME, IMPORTED_DIRNAME = "versions", "masks", "cache", "Imported"


def _atomic_write_json(path: Path, data) -> None:
    """Complete JSON to a temp file, then replace the target (an interrupted save leaves the old file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def ws_path(folder: Path, *parts: str) -> Path:
    return folder / WS_DIRNAME / Path(*parts) if parts else folder / WS_DIRNAME


def load_editor_config() -> dict:
    if EDITOR_CONFIG_PATH.exists():
        try:
            d = json.loads(EDITOR_CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(d, dict): return d
        except Exception:
            pass
    return {}


def save_editor_config(cfg: dict) -> None:
    try: _atomic_write_json(EDITOR_CONFIG_PATH, cfg)
    except Exception as ex: print(f"config save error: {ex}")


def remember_working_folder(folder: Path) -> None:
    cfg = load_editor_config()
    cfg["last_working_folder"] = str(folder)
    rec = [r for r in cfg.get("recent_working_folders", []) if isinstance(r, str)
           and os.path.normcase(os.path.abspath(r)) != os.path.normcase(os.path.abspath(str(folder)))]
    cfg["recent_working_folders"] = ([str(folder)] + rec)[:8]
    save_editor_config(cfg)


def rel_in_folder(folder: Path, p) -> str | None:
    """posix path of p relative to folder, or None when p is not inside it."""
    try:
        rel = os.path.relpath(os.path.abspath(str(p)), os.path.abspath(str(folder)))
    except ValueError:
        return None
    if rel.startswith("..") or os.path.isabs(rel): return None
    return rel.replace("\\", "/")


def safe_join(folder: Path, rel) -> Path | None:
    """folder / rel, but only if it stays inside the folder (workspace.json is data, not trusted paths)."""
    if not isinstance(rel, str) or not rel or os.path.isabs(rel): return None
    q = folder / rel
    return q if rel_in_folder(folder, q) is not None else None


def scan_root_images(folder: Path) -> dict:
    """{lower-case relative posix path: Path} of the images directly in the folder and in Imported/.
    The .image_editor directory (versions, masks, cache) is never scanned."""
    out = {}
    for sub in ("", IMPORTED_DIRNAME):
        d = folder / sub if sub else folder
        try:
            for q in d.iterdir():
                if q.is_file() and q.suffix.lower() in IMAGE_EXTS and not q.name.startswith("."):
                    out[(f"{sub}/{q.name}" if sub else q.name).lower()] = q
        except OSError:
            pass
    return out


def derive_source(folder: Path, p) -> str:
    rel = rel_in_folder(folder, p) or ""
    low = rel.lower()
    if low.startswith(f"{WS_DIRNAME}/{VERSIONS_DIRNAME}/".lower()): return "generated"
    if low.startswith(f"{IMPORTED_DIRNAME}/".lower()): return "imported"
    return "folder"


def read_workspace(folder: Path) -> dict:
    """Open a Working Folder: reconcile workspace.json with the files that exist.
      history item + file  -> restored (lineage kept);
      history item, no file -> dropped (never a permanently broken card);
      image in the folder / Imported/ that history does not know (and the user did not remove) -> new source item;
      workspace.json missing / unreadable -> every source image is still found from the filesystem.
    Files in .image_editor/versions are restored only through the metadata (never re-imported as roots)."""
    meta, corrupt = None, False
    wj = ws_path(folder, WS_FILE)
    if wj.exists():
        try:
            meta = json.loads(wj.read_text(encoding="utf-8"))
            if not isinstance(meta, dict): raise ValueError("workspace.json is not an object")
        except Exception as ex:
            print(f"workspace.json unreadable ({ex}); recovering source images from the folder")
            meta, corrupt = None, True
            try: shutil.copy2(wj, wj.with_name(f"workspace.corrupt-{int(time.time())}.json"))
            except OSError: pass
    ignored = [x for x in ((meta or {}).get("ignored") or []) if isinstance(x, str)]
    ignored_l = {x.lower() for x in ignored}
    history, seen_ids, seen_files, pruned = [], set(), set(), 0
    for it in ((meta or {}).get("history") or []):
        if not isinstance(it, dict): continue
        iid = it.get("id")
        path = safe_join(folder, it.get("file"))
        if not isinstance(iid, str) or iid in seen_ids or path is None: continue
        if not path.is_file():
            pruned += 1; continue
        seen_ids.add(iid); seen_files.add(it["file"].replace("\\", "/").lower())
        ts = it.get("ts")
        item = {"id": iid, "path": str(path), "prompt": it.get("prompt") if isinstance(it.get("prompt"), str) else "",
                "api": it.get("api") if isinstance(it.get("api"), str) else "",
                "ts": ts if isinstance(ts, (int, float)) else path.stat().st_mtime,
                "parent_id": it.get("parent_id") if isinstance(it.get("parent_id"), str) else None,
                "source": it.get("source") if it.get("source") in ("folder", "imported", "generated") else derive_source(folder, path),
                "is_temp": False}
        if isinstance(it.get("gen"), int): item["gen"] = it["gen"]
        history.append(item)
    ids = {h["id"] for h in history}
    for h in history:                                     # a parent that vanished must not dangle
        if h["parent_id"] and h["parent_id"] not in ids: h["parent_id"] = None
    new_roots = 0
    for key, q in sorted(scan_root_images(folder).items(), key=lambda kv: (kv[1].stat().st_mtime, kv[0])):
        if key in seen_files or key in ignored_l: continue
        history.append({"id": uuid.uuid4().hex, "path": str(q), "prompt": "(imported)", "api": "",
                        "ts": q.stat().st_mtime, "parent_id": None,
                        "source": "imported" if key.startswith(IMPORTED_DIRNAME.lower() + "/") else "folder",
                        "is_temp": False})
        new_roots += 1
    cur_id = (meta or {}).get("current_id")
    return {"history": history, "current_id": cur_id if cur_id in {h["id"] for h in history} else "",
            "ignored": ignored, "had_meta": meta is not None, "corrupt": corrupt,
            "new_roots": new_roots, "pruned": pruned}


def write_workspace(folder: Path, history: list, current_id: str, ignored: list) -> None:
    """Persist <folder>/.image_editor/workspace.json (atomic). Only files inside the folder are stored, always
    as relative paths — the folder can be moved or copied as a whole."""
    items = []
    for h in history:
        rel = rel_in_folder(folder, h["path"])
        if rel is None: continue
        d = {"id": h["id"], "file": rel, "source": h.get("source") or ("generated" if h.get("is_temp") else derive_source(folder, h["path"])),
             "prompt": h.get("prompt", ""), "api": h.get("api", ""), "ts": h.get("ts", 0), "parent_id": h.get("parent_id")}
        if isinstance(h.get("gen"), int): d["gen"] = h["gen"]
        items.append(d)
    keep = {i["id"] for i in items}
    _atomic_write_json(ws_path(folder, WS_FILE), {
        "version": 1, "current_id": current_id if current_id in keep else "", "history": items,
        "ignored": list(ignored)})
    for sub in (VERSIONS_DIRNAME, MASKS_DIRNAME, CACHE_DIRNAME):
        ws_path(folder, sub).mkdir(parents=True, exist_ok=True)


def unique_dest(directory: Path, name: str, reserved: set | None = None) -> Path:
    """A free destination name (see export_util.unique_dest). Pass the same `reserved` set for every file of a batch whose destinations are prepared before the first copy."""
    return _export_util.unique_dest(directory, name, reserved)

# ════════════════════════════════════════════════════════════════════════
async def main(page:ft.Page):
    page.title=f"{APP_NAME} {APP_VERSION}"; page.bgcolor=C["bg"]; page.padding=0; page.spacing=0
    _icon = APP_ROOT / "icons" / "image_editor.ico"
    if _icon.exists():
        page.window.icon = str(_icon)
    page.theme_mode = ft.ThemeMode.LIGHT

    # ── Argument mode detection ────────────────────────────────────────
    import sys as _sys
    _arg_paths = [a for a in _sys.argv[1:] if Path(a).is_file() and
                  Path(a).suffix.lower() in {".jpg",".jpeg",".png",".webp",".bmp"}]
    _arg_mode: bool = bool(_arg_paths)
    _arg_originals: set[str] = set(_arg_paths)   # paths that must not be deleted

    # Per-session OS temp for argument mode (cleaned up on close)
    _arg_session_id = uuid.uuid4().hex
    _arg_session_temp = Path(os.environ.get("TEMP", str(Path.home()))) / "WorkflowPad" / _arg_session_id
    if _arg_mode:
        _arg_session_temp.mkdir(parents=True, exist_ok=True)
        _cur_temp[0] = _arg_session_temp

    # ── State ──────────────────────────────────────────────────────────
    history:list[dict]=[]       # [{id, path, prompt, api, ts, parent_id, is_temp, saved_path}]
    prompt_hists:dict=load_global_prompt_hist()   # {"new":[], "edit":[], "mask":[]}
    current_id:list[str]=[""]
    zoom:list[float]=[1.0]
    wf_dir:  list[str] = [""]                  # the Working Folder (empty = none open)
    _ignored: list[str] = []                   # source files the user removed from History (kept on disk)
    _ws_tools: list = []                       # toolbar buttons that need a Working Folder
    _tb_labels: list = []                      # (label Text, priority) — hidden first when the toolbar is tight

    def _is_generated(item: dict) -> bool:
        return item.get("source") == "generated" or bool(item.get("is_temp"))

    def cur()->dict|None:
        return next((h for h in history if h["id"]==current_id[0]),None)

    def _add_ph(p:str, img_path:str|None=None, dialog:str="new", meta:dict|None=None):
        """Save prompt + thumbnail to the history of that dialog"""
        p=p.strip()
        if not p or p=="(imported)": return
        if p.startswith("["): return
        key = dialog if dialog in _HIST_KEYS else "new"
        lst = prompt_hists[key]
        thumb_b64 = None
        src = img_path or (cur()["path"] if cur() else None)
        if src:
            try:
                from PIL import Image as PI
                im = PI.open(src)
                im.thumbnail((600,600), PI.LANCZOS)
                import io, base64
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=80)
                thumb_b64 = base64.b64encode(buf.getvalue()).decode()
            except: pass
        entry = {"text": p, "thumb": thumb_b64}
        old = next((e for e in lst if isinstance(e,dict) and e.get("text") == p), None)
        if old:                                    # same prompt re-saved: keep what we already know about it
            if not thumb_b64: entry["thumb"] = old.get("thumb")
            entry.update({k: old[k] for k in _HIST_META_KEYS if k in old})
        if meta: entry.update(meta)
        lst[:] = [e for e in lst if (e["text"] if isinstance(e,dict) else e) != p]
        lst.insert(0, entry)
        if len(lst) > 100: lst.pop()
        save_global_prompt_hist(prompt_hists)

    def _autosave():
        """History / state changed -> save <folder>/.image_editor/workspace.json (atomic). No-op in argument mode."""
        if not wf_dir[0]: return
        try:
            write_workspace(Path(wf_dir[0]), history, current_id[0], _ignored)
        except Exception as ex:
            print(f"workspace save error: {ex}")

    def _mark_dirty():
        _update_title()
        _autosave()

    def _update_title():
        name = (Path(wf_dir[0]).name or wf_dir[0]) if wf_dir[0] else ""
        page.title = f"{APP_NAME} {APP_VERSION} — {name}" if name else f"{APP_NAME} {APP_VERSION}"

    # ── SNACKBARS ─────────────────────────────────────────────────────
    def show_snack(msg:str,color:str=C["green"],duration:int=2500):
        snack=ft.SnackBar(content=ft.Text(msg,color="#111827",size=12),bgcolor=color,duration=duration)
        page.overlay.append(snack); snack.open=True; page.update()

    # ── Viewer state ──────────────────────────────────────────────────
    main_img=ft.Image(src="",fit=ft.BoxFit.CONTAIN,
                      width=None,height=None)
    empty_title=ft.Text("Choose a Working Folder to get started",size=14,color=C["text3"])
    empty_sub=ft.Text("or drop images after opening a folder",size=11,color=C["text3"],italic=True)
    empty_open_btn=ft.FilledButton(
        content=ft.Row([ft.Icon(ft.Icons.FOLDER_OPEN_OUTLINED,size=14,color="#FFFFFF"),
                        ft.Text("Open Folder",size=11,color="#FFFFFF")],tight=True,spacing=5),
        bgcolor=C["accent"],on_click=lambda e: asyncio.create_task(_pick_folder()))
    empty_hint=ft.Column([
        ft.Icon(ft.Icons.PHOTO_SIZE_SELECT_LARGE_OUTLINED,size=64,color=C["text3"]),
        empty_title, empty_open_btn, empty_sub,
    ],horizontal_alignment=ft.CrossAxisAlignment.CENTER,spacing=10,visible=True)

    def _refresh_empty_hint():
        if wf_dir[0]:
            empty_title.value="No images yet"
            empty_sub.value="New  •  Import  •  Drop images here"
            empty_open_btn.visible=False
        else:
            empty_title.value="Choose a Working Folder to get started"
            empty_sub.value="or drop images after opening a folder"
            empty_open_btn.visible=True

    zoom_lbl=ft.Text("100%",size=10,color=C["text2"])
    _img_px:list[int]=[0,0]                         # pixel size of the image on screen

    def _fit_factor()->float:
        """Scale at which the picture is shown at 'Fit' (never above 1:1)."""
        ow,oh=_img_px; vw,vh=view_zoom.vw,view_zoom.vh
        if ow<=0 or oh<=0 or vw<=0 or vh<=0: return 1.0
        return min(1.0,vw/ow,vh/oh)

    def _refresh_zoom_lbl():
        zoom_lbl.value=f"{round(_fit_factor()*view_zoom.scale*100)}%"
        try: zoom_lbl.update()
        except Exception: pass

    def zoom_in(e=None):  view_zoom.zoom_at(1.25)
    def zoom_out(e=None): view_zoom.zoom_at(1/1.25)
    def zoom_fit(e=None): view_zoom.reset()
    def zoom_actual(e=None):
        """Click on the % label: show the image at its real pixel size (100%)."""
        fit=_fit_factor()
        view_zoom.reset()
        if fit<1.0: view_zoom.zoom_at(1/fit)

    def _on_wheel(e:ft.ScrollEvent):
        if not _img_layer.visible: return
        view_zoom.zoom_at(1.15 if e.scroll_delta.y<0 else 1/1.15,e.local_position.x,e.local_position.y)

    def _on_pan(e:ft.DragUpdateEvent):
        if _img_layer.visible: view_zoom.pan_by(e.local_delta.x,e.local_delta.y)

    def _on_vp_size(e):
        view_zoom.set_viewport(e.width,e.height)

    _stack_holder=ft.Container(
        content=ft.Stack([
            ft.Container(
                content=ft.Column([empty_hint],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER),
                expand=True,alignment=ft.Alignment(0,0),
            ),
            ft.Container(content=main_img,expand=True,
                         alignment=ft.Alignment(0,0),visible=False),
        ]),
        expand=True,
    )
    _xf=ft.Container(content=_stack_holder,expand=True,
                     scale=ft.Scale(scale=1.0,alignment=ft.Alignment(-1,-1)),offset=ft.Offset(0,0))
    viewer_scroll=ft.Container(content=_xf,expand=True,clip_behavior=ft.ClipBehavior.HARD_EDGE)
    view_zoom=_Zoomer(_xf,on_change=lambda: _refresh_zoom_lbl())
    zoom_lbl_btn=ft.Container(content=zoom_lbl,on_click=zoom_actual,tooltip="Click: actual size (100%)",
                              padding=po(left=4,right=4,top=4,bottom=4),border_radius=br(4))
    _empty_layer=_stack_holder.content.controls[0]
    _img_layer  =_stack_holder.content.controls[1]

    img_prompt_lbl=ft.Text("",size=11,color=C["text2"],max_lines=2,overflow=ft.TextOverflow.ELLIPSIS)
    img_info_lbl  =ft.Text("",size=9, color=C["text3"],italic=True)
    temp_badge    =ft.Container(
        content=ft.Text("TEMP – Unsaved",size=10,color="#111827",weight=ft.FontWeight.W_600),
        bgcolor=C["amber"],border_radius=br(4),
        padding=po(left=8,right=8,top=3,bottom=3),visible=False,
    )
    save_as_btn_bar=ft.FilledButton(
        content=ft.Text("💾 Save As...",size=11,color="#111827"),
        bgcolor=C["green"],height=30,visible=False,
    )

    info_bar=ft.Container(
        content=ft.Row([
            ft.Column([img_prompt_lbl,img_info_lbl],spacing=2,expand=True),
            ft.Row([temp_badge],spacing=8,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ],spacing=12,vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor=C["surface"],padding=po(left=14,right=14,top=8,bottom=8),
        border=ft.Border(top=ft.BorderSide(1,C["border"])),visible=False,
    )

    def load_image(item:dict|None):
        if not item or not Path(item["path"]).exists():
            _empty_layer.visible=True; _img_layer.visible=False
            _img_px[:]=[0,0]; view_zoom.reset()
            _refresh_empty_hint()
            img_prompt_lbl.value=""; img_info_lbl.value=""
            info_bar.visible=False
            temp_badge.visible=False; save_as_btn_bar.visible=False
        else:
            main_img.src=_b64(item["path"])
            main_img.width=None; main_img.height=None
            try:
                from PIL import Image as _PI
                with _PI.open(item["path"]) as _im: _img_px[:]=list(_im.size)
            except Exception: _img_px[:]=[0,0]
            view_zoom.reset()
            _empty_layer.visible=False; _img_layer.visible=True
            ts=time.strftime("%d/%m/%y %H:%M",time.localtime(item["ts"]))
            img_prompt_lbl.value=item.get("prompt","")
            is_t=bool(item.get("is_temp")) and _arg_mode          # TEMP only exists in argument mode now
            img_info_lbl.value=f"{Path(item['path']).name}  •  {ts}"+("  •  [TEMP]" if is_t else "")
            temp_badge.visible=is_t
            save_as_btn_bar.visible=is_t or (not _arg_mode and _is_generated(item))   # versions can be exported
            info_bar.visible=True
        page.update()

    # ── Save As ────────────────────────────────────────────────────────
    async def do_save_as(e=None):
        item=cur()
        if not item: return
        fp=ft.FilePicker()
        result=await fp.save_file(
            dialog_title="Save As",
            file_name=f"edit_{time.strftime('%Y%m%d_%H%M%S')}.jpg",
            allowed_extensions=["jpg","jpeg","png"],
        )
        if result:
            dst=Path(result)
            await _export_util.copy_one(item["path"],dst)         # in a worker thread: the window stays responsive
            _replaced=bool(_arg_mode and item.get("is_temp"))   # an original opened from the command line is never re-pointed at the copy
            if _replaced:                                     # argument mode: the saved file replaces the TEMP result
                item["path"]=str(dst); item["is_temp"]=False
                if "saved_path" not in item: item["saved_path"]=str(dst)
                _mark_dirty()
                load_image(item)
                rebuild_history()
            show_snack(f"✓ Saved {dst.name}" if _replaced else f"✓ Exported a copy: {dst.name}")

    save_as_btn_bar.on_click=do_save_as

    # ── Fullscreen ─────────────────────────────────────────────────────
    # Esc closes it; +/- zoom, 0 fits; the wheel zooms about the cursor; double-click fits. The close button is white on
    # a dark chip (it used to be dark-on-dark, so there was no visible way out).
    fs_img  = ft.Image(src="", fit=ft.BoxFit.CONTAIN, expand=True)
    fs_info_lbl = ft.Text("", size=11, color="#FFFFFFCC")

    fs_xf = ft.Container(content=ft.Container(content=fs_img, expand=True, alignment=ft.Alignment(0,0)), expand=True,
                         scale=ft.Scale(scale=1.0, alignment=ft.Alignment(-1,-1)), offset=ft.Offset(0,0))
    fs_zoom = _Zoomer(fs_xf)
    fs_zoom.ZMAX = 20.0

    def _fs_close(e=None):
        fs_overlay.visible = False; page.update()

    def _fs_wheel(e:ft.ScrollEvent):
        fs_zoom.zoom_at(1.15 if e.scroll_delta.y < 0 else 1/1.15, e.local_position.x, e.local_position.y)

    def _fs_pan(e:ft.DragUpdateEvent):
        fs_zoom.pan_by(e.local_delta.x, e.local_delta.y)

    def _fs_in(e=None):  fs_zoom.zoom_at(1.25)
    def _fs_out(e=None): fs_zoom.zoom_at(1/1.25)
    def _fs_fit(e=None): fs_zoom.reset()

    def _fs_btn(icon, tip, cb):
        return ft.IconButton(icon=icon, icon_color="#FFFFFF", icon_size=20, tooltip=tip, on_click=cb, width=36, height=36, padding=0)

    fs_overlay = ft.Container(
        content=ft.Stack([
            ft.Container(
                content=ft.GestureDetector(content=ft.Container(fs_xf, expand=True, clip_behavior=ft.ClipBehavior.HARD_EDGE),
                                           on_scroll=_fs_wheel, on_pan_update=_fs_pan, on_double_tap=_fs_fit, expand=True),
                expand=True, on_size_change=lambda e: fs_zoom.set_viewport(e.width, e.height),
            ),
            ft.Container(
                content=fs_info_lbl, bgcolor="#00000088",
                padding=ft.Padding(left=12, right=12, top=6, bottom=6),
                border_radius=ft.BorderRadius(0,0,8,0), left=0, top=0,
            ),
            ft.Container(
                content=ft.Row([
                    _fs_btn(ft.Icons.ZOOM_OUT_ROUNDED, "Zoom out (-)", _fs_out),
                    _fs_btn(ft.Icons.ZOOM_IN_ROUNDED, "Zoom in (+)", _fs_in),
                    _fs_btn(ft.Icons.FIT_SCREEN_ROUNDED, "Fit (0 / double-click)", _fs_fit),
                    ft.Container(width=1, height=20, bgcolor="#FFFFFF55"),
                    _fs_btn(ft.Icons.CLOSE_ROUNDED, "Close (Esc)", _fs_close),
                ], spacing=2, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                bgcolor="#000000AA", border_radius=br(10), padding=po(left=6, right=6, top=2, bottom=2),
                right=10, top=10,
            ),
        ]),
        bgcolor="#111827", expand=True, visible=False,
    )
    page.overlay.append(fs_overlay)

    def open_fullscreen(e=None):
        item = cur()
        if not item: return
        try:
            from PIL import Image as PI
            im = PI.open(item["path"]); iw,ih = im.size
            fs_info_lbl.value = f"{Path(item['path']).name}  •  {iw}×{ih}  •  Esc to close"
        except:
            fs_info_lbl.value = Path(item["path"]).name + "  •  Esc to close"
        fs_img.src = _b64(item["path"])
        fs_zoom.reset()
        fs_overlay.visible = True
        page.update()

    def _on_key(e: ft.KeyboardEvent):
        """Esc leaves fullscreen; + / - / 0 zoom while it is open. Ignored otherwise (text fields keep their keys)."""
        if not fs_overlay.visible: return
        k = (e.key or "").lower()
        if k in ("escape", "esc"): _fs_close()
        elif k in ("+", "=", "equal", "numpad add", "add"): fs_zoom.zoom_at(1.25)
        elif k in ("-", "minus", "numpad subtract", "subtract"): fs_zoom.zoom_at(1/1.25)
        elif k in ("0", "digit 0", "numpad 0"): fs_zoom.reset()

    page.on_keyboard_event = _on_key

    # ── Dialogs ────────────────────────────────────────────────────────
    def _dlg_input(hint,multiline=False,min_lines=1,max_lines=999,value="",field_height=None,expand=False):
        kw = dict(value=value,multiline=multiline,min_lines=min_lines,
            hint_text=hint,text_size=12,color=C["text1"],hint_style=ft.TextStyle(color=C["text3"]),
            border_color=C["border"],focused_border_color=C["accent"],bgcolor="#FFFFFF",
            content_padding=po(left=10,right=10,top=8,bottom=8),cursor_color=C["accent"],
            height=field_height)
        if max_lines is not None: kw["max_lines"] = max_lines
        tf = ft.TextField(**kw)
        if expand: tf.expand = True
        return tf

    def _prompt_editor(value=""):
        """Create prompt editor — dark-style TextField with word wrap"""
        return ft.TextField(
            value=value,
            multiline=True,
            min_lines=6,
            max_lines=None,
            expand=True,
            text_style=ft.TextStyle(
                font_family="Consolas, monospace",
                size=13,
                color="#111827",          # Atom One Dark text color
                letter_spacing=0.3,
            ),
            hint_text="Enter prompt...",
            hint_style=ft.TextStyle(color="#5C6370", size=13),
            bgcolor="#FFFFFF",            # Atom One Dark background
            border_color="#D1D5DB",
            focused_border_color="#2563EB",
            border_radius=ft.BorderRadius(4,4,4,4),
            content_padding=ft.Padding(left=14, right=14, top=12, bottom=12),
            cursor_color="#2563EB",
            cursor_width=2,
            selection_color="#D1D5DB",
        )

    def _dlg_dd(options,hint):
        dd = ft.Dropdown(options=options,hint_text=hint,text_size=12,height=42,
            color="#111827",hint_style=ft.TextStyle(color=C["text3"]),
            border_color=C["border"],focused_border_color=C["accent"],
            bgcolor="#FFFFFF",
            content_padding=po(left=10,right=4,top=0,bottom=0))
        def _on_sel(e):
            setattr(dd,"value",e.control.value)
            show_snack(f"✓ Selected: {e.control.value}",C["accent"],duration=1500)
        dd.on_select = _on_sel
        return dd

    def _section(label):
        return ft.Text(label,size=10,color=C["text2"],weight=ft.FontWeight.W_600)

    # ── Progress toast bar (pinned at the top) ───────────────────────────────
    prog_status_lbl = ft.Text("⏳", size=12, color=C["amber"], weight=ft.FontWeight.W_600)
    prog_prompt_lbl = ft.Text("", size=11, color=C["text2"],
                               max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True)
    prog_ring       = ft.ProgressRing(width=18, height=18, stroke_width=2, color=C["amber"])
    async def _stop_generation(e=None):
        _prog_stop.set()
        prog_overlay.visible = False
        page.update()
        for _pid in list(_ACTIVE_PIDS):
            await asyncio.get_event_loop().run_in_executor(None, comfy_cancel, _pid)

    prog_cancel_btn = ft.FilledButton(
        content=ft.Row([ft.Icon(ft.Icons.STOP_ROUNDED, size=14, color="#FFFFFF"),
                        ft.Text("Stop", size=11, color="#FFFFFF", weight=ft.FontWeight.W_600)],
                       tight=True, spacing=4),
        on_click=_stop_generation,
        bgcolor=C["red"],
        tooltip="Stop generating and cancel the ComfyUI job",
        style=ft.ButtonStyle(padding=po(left=10, right=10, top=4, bottom=4),
                             shape=ft.RoundedRectangleBorder(radius=6)),
    )

    prog_overlay = ft.Container(
        content=ft.Row([
            prog_ring,
            prog_status_lbl,
            prog_prompt_lbl,
            prog_cancel_btn,
        ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor="#1E1E1E",
        border=ft.Border(bottom=ft.BorderSide(2, C["amber"])),
        padding=po(left=16, right=12, top=10, bottom=10),
        visible=False,
    )
    page.overlay.append(prog_overlay)

    def _show_progress(prompt: str, label: str):
        _prog_stop.clear()
        prog_status_lbl.value = label
        prog_prompt_lbl.value = prompt[:120]
        prog_overlay.visible = True; page.update()

    def _hide_progress():
        prog_overlay.visible = False; page.update()

    def _update_progress(txt: str):
        prog_status_lbl.value = txt; page.update()

    # ── Prompt history dialog ──────────────────────────────────────────
    hist_list_col = ft.Column(scroll=ft.ScrollMode.AUTO,spacing=4,expand=True)
    _hist_dlg_target: list = []   # [prompt_field ref]
    _hist_dlg_key: list[str] = ["new"]  # dialog currently open

    def close_hist_dlg(e=None): hist_sel_dlg.open=False; page.update()

    _hist_filter: list[str] = [""]
    _thumb_tried: set = set()          # prompts whose missing thumbnail was already looked up (once per session)
    _hist_dirty: list[bool] = [False]  # a thumbnail was back-filled -> persist once, not per keystroke

    def _meta_line(e) -> str:
        """'api.json · 2304×1296 · 30 steps · 2026-10-11 14:05' from whatever metadata the entry has."""
        if not isinstance(e, dict): return ""
        parts = []
        if e.get("api"): parts.append(str(e["api"]))
        if e.get("width") and e.get("height"): parts.append(f"{e['width']}×{e['height']}")
        if e.get("api"): parts.append(f"{e['steps']} steps" if e.get("steps") else "default steps")
        if e.get("created_at"):
            try: parts.append(time.strftime("%Y-%m-%d %H:%M", time.localtime(e["created_at"])))
            except Exception: pass
        return " · ".join(parts)

    def _restore_new_meta(e: dict):
        """Restore workflow / size / steps of a New-image history entry; warn instead of failing."""
        notes = []
        api = e.get("api")
        if api and api in [o.key for o in new_api_dd.options]:
            new_api_dd.value = api
        elif api:
            notes.append(f"workflow '{api}' no longer exists")
        size_lbl = next((l for l, w, h in _ALL_SIZES
                         if (w, h) == (e.get("width"), e.get("height"))), None)
        if size_lbl: new_size_dd.value = size_lbl
        elif e.get("width"): notes.append("image size not available")
        want = e.get("steps")
        info = _refresh_steps(want)
        if want and info["target"] is not None and info["value"] is not None                 and new_steps_tf.value != str(want):
            notes.append(f"steps {want} not valid here, using {new_steps_tf.value}")
        elif want and info["target"] is None:
            notes.append("workflow has no editable steps")
        if notes: show_snack("⚠ " + "; ".join(notes), C["amber"], duration=4000)

    def _rebuild_hist_list(filter_text="", dialog:str|None=None):
        if dialog is not None: _hist_dlg_key[0] = dialog
        _hist_filter[0] = filter_text.lower()
        hist_list_col.controls.clear()
        def _txt(e): return e["text"] if isinstance(e,dict) else e
        def _thumb(e): return e.get("thumb") if isinstance(e,dict) else None
        lst = prompt_hists[_hist_dlg_key[0]]
        visible = [e for e in lst if not _txt(e).startswith("[")]
        filtered = [e for e in visible if _hist_filter[0] in _txt(e).lower()] if _hist_filter[0] else visible
        if not filtered:
            hist_list_col.controls.append(
                ft.Text("Not found" if _hist_filter[0] else "No prompt history yet",
                        size=11,color=C["text3"],italic=True))
        else:
            for i,entry in enumerate(filtered):
                p = _txt(entry); thumb = _thumb(entry)
                # If no thumb in entry, look it up from history card using this prompt
                if not thumb and p not in _thumb_tried:
                    _thumb_tried.add(p)
                    for h in reversed(history):
                        hp = (h.get("prompt","") or "").strip()
                        if hp == p or hp == f"[MASK] {p}" or p in hp:
                            try:
                                if Path(h["path"]).exists():
                                    from PIL import Image as PI
                                    import io, base64
                                    im = PI.open(h["path"])
                                    im.thumbnail((600,600), PI.LANCZOS)
                                    buf = io.BytesIO()
                                    im.save(buf, format="JPEG", quality=80)
                                    thumb = base64.b64encode(buf.getvalue()).decode()
                                    # Save back into entry
                                    if isinstance(entry, dict): entry["thumb"] = thumb; _hist_dirty[0] = True
                                    break
                            except: pass

                def _sel(e,_p=p,_e=entry):
                    if _hist_dlg_target: _hist_dlg_target[0].value=_p
                    close_hist_dlg()
                    if _hist_dlg_key[0] == "new" and isinstance(_e, dict) and _e.get("api"):
                        _restore_new_meta(_e)
                    page.update()
                def _del_ph(e,_entry=entry):
                    lst2 = prompt_hists[_hist_dlg_key[0]]
                    if _entry in lst2: lst2.remove(_entry)
                    save_global_prompt_hist(prompt_hists)
                    _rebuild_hist_list(_hist_filter[0]); page.update()

                # Thumbnail — small on the right, hover to enlarge
                thumb_ctrl = ft.Container(width=0)
                if thumb:
                    thumb_img = ft.Image(
                        src=f"data:image/jpeg;base64,{thumb}",
                        width=56, height=42, fit=ft.BoxFit.COVER,
                        border_radius=br(4),
                    )
                    thumb_big = ft.Image(
                        src=f"data:image/jpeg;base64,{thumb}",
                        width=240, height=180, fit=ft.BoxFit.CONTAIN,
                    )
                    thumb_ctrl = ft.Container(
                        content=ft.Image(
                            src=f"data:image/jpeg;base64,{thumb}",
                            width=80, height=60, fit=ft.BoxFit.COVER,
                            border_radius=br(4),
                        ),
                        border_radius=br(4),
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
                        width=80, height=60,
                    )

                hist_list_col.controls.append(
                    ft.Container(
                        content=ft.Row([
                            ft.Container(
                                content=ft.Text(str(i+1),size=9,color=C["accent"],
                                               weight=ft.FontWeight.W_700),
                                width=20,height=20,bgcolor="#DBEAFE",
                                border_radius=br(10),alignment=ft.Alignment(0,0),
                            ),
                            ft.Column([
                                # not selectable: a selectable Text is its own scroll/gesture area, it swallowed the mouse
                                # wheel and clicks; the whole row is the click target instead
                                ft.Text(p,size=11,color=C["text1"],selectable=False,
                                        max_lines=3,overflow=ft.TextOverflow.ELLIPSIS),
                                *([ft.Text(_meta_line(entry),size=10,color=C["text3"],
                                           max_lines=1,overflow=ft.TextOverflow.ELLIPSIS)]
                                  if _meta_line(entry) else []),
                            ],spacing=3,expand=True),
                            thumb_ctrl,
                            ft.IconButton(
                                icon=ft.Icons.DELETE_OUTLINE_ROUNDED,
                                icon_color=C["text3"],icon_size=15,
                                on_click=_del_ph,width=26,height=26,padding=0,
                                tooltip="Delete prompt",
                            ),
                        ],spacing=8,vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        bgcolor="#F9FAFB",border_radius=br(6),
                        padding=po(left=10,right=6,top=8,bottom=8),
                        on_click=_sel,ink=True,
                        border=ft.Border(top=ft.BorderSide(1,C["border"]),
                                        right=ft.BorderSide(1,C["border"]),
                                        bottom=ft.BorderSide(1,C["border"]),
                                        left=ft.BorderSide(1,C["border"])),
                    )
                )

    def _flush_hist():
        if _hist_dirty[0]:
            _hist_dirty[0] = False; save_global_prompt_hist(prompt_hists)

    _hist_title_lbl = ft.Text("Prompt History",size=13,weight=ft.FontWeight.W_700,color=C["text1"])

    def _clear_confirmed(e):
        prompt_hists[_hist_dlg_key[0]].clear()     # only the history type that is open
        save_global_prompt_hist(prompt_hists)
        clear_confirm_dlg.open = False
        _rebuild_hist_list(); page.update()

    def _clear_cancel(e=None):
        clear_confirm_dlg.open = False; page.update()

    clear_confirm_dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("Clear history?", size=14, weight=ft.FontWeight.W_700, color=C["text1"]),
        content=ft.Text("", size=12, color=C["text2"]),
        actions=[ft.TextButton("Cancel", on_click=_clear_cancel, style=ft.ButtonStyle(color=C["text2"])),
                 ft.TextButton("Clear History", on_click=_clear_confirmed, style=ft.ButtonStyle(color=C["red"]))],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(clear_confirm_dlg)

    def _do_clear_hist(e):
        n = len(prompt_hists[_hist_dlg_key[0]])
        clear_confirm_dlg.content.value = (f"Delete all {n} saved prompt(s) in "
            f"\"{_HIST_LABELS.get(_hist_dlg_key[0], 'Prompt History')}\"? Other history types are not affected.")
        clear_confirm_dlg.open = True; page.update()

    hist_sel_dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row([
            ft.Icon(ft.Icons.HISTORY_ROUNDED,color=C["accent"],size=16),
            _hist_title_lbl,
            ft.Container(expand=True),
            ft.TextButton("Clear all",
                on_click=_do_clear_hist,
                style=ft.ButtonStyle(color=C["red"]),
            ),
            ft.IconButton(icon=ft.Icons.CLOSE_ROUNDED,icon_color=C["text2"],icon_size=18,
                         on_click=close_hist_dlg,width=30,height=30,padding=0),
        ],spacing=8,vertical_alignment=ft.CrossAxisAlignment.CENTER),
        content=ft.Container(
            content=ft.Column([
                ft.TextField(
                    hint_text="🔍 Search...", text_size=12, height=36,
                    color=C["text1"], hint_style=ft.TextStyle(color=C["text3"]),
                    border_color=C["border"], focused_border_color=C["accent"],
                    bgcolor="#FFFFFF", content_padding=po(left=10,right=10,top=4,bottom=4),
                    on_change=lambda e: (_rebuild_hist_list(e.control.value), page.update()),
                ),
                hist_list_col,
            ], spacing=8, expand=True),
            width=720,height=560,bgcolor=C["panel"],
            border_radius=br(8),padding=po(left=12,right=12,top=12,bottom=12),
        ),
        bgcolor=C["surface"],
        actions=[ft.TextButton("Close",on_click=close_hist_dlg,
                              style=ft.ButtonStyle(color=C["text2"]))],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.overlay.append(hist_sel_dlg)

    _HIST_LABELS = {"new": "New – Prompt History", "edit": "Edit – Prompt History", "mask": "Mask – Prompt History"}

    def _open_hist_dlg(target_field, dialog: str = "new"):
        _hist_dlg_target.clear(); _hist_dlg_target.append(target_field)
        _hist_title_lbl.value = _HIST_LABELS.get(dialog, "Prompt History")
        _rebuild_hist_list("", dialog); _flush_hist()
        hist_sel_dlg.open=True; page.update()

    def _hist_btn(target_field, dialog: str = "new"):
        return ft.OutlinedButton(
            content=ft.Row([ft.Icon(ft.Icons.HISTORY_ROUNDED,size=13,color=C["text2"]),
                            ft.Text("Prompt History",size=11,color=C["text2"])],
                           tight=True,spacing=5),
            on_click=lambda e:_open_hist_dlg(target_field, dialog),height=32,
            style=ft.ButtonStyle(side=ft.BorderSide(1,C["border"]),color=C["text2"]),
        )

    # ── New image dialog ───────────────────────────────────────────────
    new_api_dd  = _dlg_dd([ft.dropdown.Option(f) for f in scan_create_apis()],"Select workflow")
    new_size_dd = ft.Dropdown(
        value=_DEFAULT_SIZE,
        options=[ft.dropdown.Option(lbl) for lbl,*_ in _ALL_SIZES],
        text_size=12, height=42,
        color="#111827", hint_style=ft.TextStyle(color=C["text3"]),
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor="#FFFFFF",
        content_padding=po(left=10,right=4,top=0,bottom=0),
    )
    def _on_new_size_sel(e):
        setattr(new_size_dd,"value",e.control.value); page.update()
    new_size_dd.on_select = _on_new_size_sel
    new_prompt  = _prompt_editor()
    new_status  = ft.Text("",size=11,color=C["red"],italic=True)

    # Steps: shown only when the selected workflow has exactly one editable steps field
    new_steps_tf = ft.TextField(
        hint_text="Default", hint_style=ft.TextStyle(color=C["text3"]), text_size=12, height=42,
        color="#111827", border_color=C["border"], focused_border_color=C["accent"],
        bgcolor="#FFFFFF", content_padding=po(left=10,right=10,top=0,bottom=0),
        text_vertical_align=ft.VerticalAlignment.CENTER,
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.NumbersOnlyInputFilter(), read_only=True,   # read_only keeps the normal field look (disabled looked "floating")
    )
    _steps_info: dict = {"value": None, "editable": False}

    def _refresh_steps(preferred=None):
        """Re-read the selected workflow; `preferred` (e.g. from History) wins when valid."""
        info = read_workflow_steps(new_api_dd.value) if new_api_dd.value else                {"target": None, "value": None, "reason": ""}
        ok = info["target"] is not None
        _steps_info["editable"] = ok; _steps_info["value"] = info["value"]
        new_steps_tf.read_only = not ok
        use = preferred if (ok and _literal_int(preferred) and 1 <= preferred <= _STEPS_MAX) else info["value"]
        new_steps_tf.value = str(use) if ok else ""
        new_steps_tf.tooltip = None if ok else (info["reason"] or "Workflow default")
        return info

    def _selected_steps():
        """Steps to inject, or None (keep the workflow's own value)."""
        if not _steps_info["editable"]: return None
        try: v = int((new_steps_tf.value or "").strip())
        except ValueError: return None
        return v if 1 <= v <= _STEPS_MAX else None

    _orig_api_sel = new_api_dd.on_select
    def _on_new_api_sel(e):
        _orig_api_sel(e); _refresh_steps(); page.update()
    new_api_dd.on_select = _on_new_api_sel

    # (close_new ตัวจริงอยู่ที่ New image overlay ด้านล่าง — ตัวเดิมที่อ้าง new_dlg เป็นของ
    #  ยุคที่ยังเป็น AlertDialog ถูกนิยามทับก่อนถูกเรียกอยู่แล้ว จึงเป็นโค้ดตายที่อ้างชื่อที่ไม่มี)

    async def do_new(e):
        api=new_api_dd.value; prompt=(new_prompt.value or "").strip()
        if not api: new_status.value="⚠ Select a workflow first"; page.update(); return
        if not prompt: new_status.value="⚠ Enter a prompt first"; page.update(); return
        close_new()
        _show_progress(prompt,"⏳ Generating...")
        def _p(t): _update_progress(t)
        try:
            result=await run_comfy(api,prompt,on_progress=_p)
            _hide_progress()
            if result:
                _add_ph(prompt, dialog="new")
                item={"id":uuid.uuid4().hex,"path":result,"prompt":prompt,"api":api,
                      "ts":time.time(),"parent_id":None,"is_temp":True,"gen":_gen_count[0]}
                history.append(item); current_id[0]=item["id"]
                _mark_dirty(); load_image(item); rebuild_history()
                show_snack("✓ Generate completed")
            else: show_snack("⚠ No image returned from ComfyUI",C["red"])
        except Exception as ex:
            _hide_progress(); show_snack(f"⚠ {ex}",C["red"])

    def _panel_header(icon, label, color, on_close):
        return ft.Container(
            content=ft.Row([
                ft.Icon(icon, color=color, size=16),
                ft.Text(label, size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
                ft.Container(expand=True),
                ft.IconButton(icon=ft.Icons.CLOSE_ROUNDED, icon_color=C["text3"],
                              icon_size=18, on_click=on_close, width=30, height=30, padding=0),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=po(left=20, right=12, top=14, bottom=14),
            border=ft.Border(bottom=ft.BorderSide(1, C["border"])),
        )

    def _panel_actions(*btns):
        return ft.Container(
            content=ft.Row(list(btns), spacing=8,
                           alignment=ft.MainAxisAlignment.END),
            padding=po(left=20, right=20, top=12, bottom=14),
            border=ft.Border(top=ft.BorderSide(1, C["border"])),
        )

    def _make_overlay_panel(*body_children, visible=False):
        """Create a full-screen overlay panel — content area between the header and action bar"""
        col = ft.Column(list(body_children), spacing=10,
                        scroll=ft.ScrollMode.AUTO, expand=True)
        body = ft.Container(content=col, expand=True,
                            padding=po(left=20, right=20, top=16, bottom=8))
        return body, ft.Container(
            content=None,   # set outside
            bgcolor="#00000044", expand=True,
            alignment=ft.Alignment(0, 0), visible=visible,
        )

    # ── New image overlay ──────────────────────────────────────────────
    def close_new(e=None):
        new_overlay.visible = False; page.update()


    # ── History panel state ────────────────────────────────────────────
    _hist_tab: list[str] = ["all"]   # "all" | "fav"
    _batch_size: list[int] = [1]     # number of images sent per run (persistent)
    _favorites: set = set()          # set of item["id"] that are favorites
    _selected_ids: set = set()       # multi-select
    _gen_count: list[int] = [0]      # generation round counter
    _last_api: dict = {"new": None, "edit": None, "mask": None, "extend": None, "enhance": None, "caption": None, "scale": None}
    _last_prompt: dict = {"new": "", "edit": "", "mask": ""}

    def _mk_batch_ctrl():
        """Create a +/- batch size control"""
        n_ref: list[int] = [1]
        num_txt = ft.Text("1", size=13, color=C["text1"], width=24,
                          text_align=ft.TextAlign.CENTER, weight=ft.FontWeight.W_600)
        def _update(delta):
            n_ref[0] = max(1, min(20, n_ref[0] + delta))
            num_txt.value = str(n_ref[0])
            _batch_size[0] = n_ref[0]
            page.update()
        ctrl = ft.Row([
            ft.IconButton(ft.Icons.REMOVE_ROUNDED, icon_size=14, icon_color=C["text2"],
                width=26, height=26, on_click=lambda e: _update(-1)),
            num_txt,
            ft.IconButton(ft.Icons.ADD_ROUNDED, icon_size=14, icon_color=C["text2"],
                width=26, height=26, on_click=lambda e: _update(1)),
        ], spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        ctrl.get_value = lambda: n_ref[0]
        return ctrl

    _batch_dd_new  = _mk_batch_ctrl()
    _batch_dd_edit = _mk_batch_ctrl()
    _batch_dd_mask = _mk_batch_ctrl()

    async def do_new(e):
        api = new_api_dd.value; prompt = (new_prompt.value or "").strip()
        if not api:   new_status.value = "⚠ Select a workflow first"; page.update(); return
        if not prompt: new_status.value = "⚠ Enter a prompt first"; page.update(); return
        close_new()
        n = _batch_dd_new.get_value()
        _batch_size[0] = n
        print(f"[BATCH] do_new n={n}")
        _gen_count[0] += 1
        gen_w, gen_h = _size_map.get(new_size_dd.value or _DEFAULT_SIZE, _size_map[_DEFAULT_SIZE])
        gen_steps = _selected_steps()               # snapshot: the values actually sent, not live UI
        hist_meta = {"api": api, "width": gen_w, "height": gen_h, "steps": gen_steps, "created_at": time.time()}
        _add_ph(prompt, dialog="new", meta=hist_meta)
        for i in range(n):
            resolved_prompt = resolve_wildcards(prompt)
            _show_progress(resolved_prompt, f"⏳ Generate {i+1}/{n}...")
            def _p(t): _update_progress(t)
            try:
                result = await run_comfy(api, resolved_prompt, on_progress=_p,
                                          width=gen_w, height=gen_h, steps=gen_steps)
                _hide_progress()
                if result:
                    _add_ph(resolved_prompt, result, dialog="new", meta=hist_meta)
                    item = {"id": uuid.uuid4().hex, "path": result, "prompt": resolved_prompt, "api": api,
                            "ts": time.time(), "parent_id": None, "is_temp": True, "gen": _gen_count[0]}
                    history.append(item); current_id[0] = item["id"]
                    _mark_dirty(); load_image(item); rebuild_history()
                else: show_snack(f"⚠ Image {i+1} failed", C["red"]); break
            except Exception as ex:
                import traceback; traceback.print_exc()
                _hide_progress(); show_snack(f"⚠ {ex}", C["red"]); break
        show_snack(f"✓ Generate completed {n} image(s)" if n > 1 else "✓ Generate completed")

    # Compact modal: fixed height (clamped to the window), prompt sized in lines. Chrome = header + settings row +
    # prompt label row + paddings + the field's own padding + footer (~320px, measured from a screenshot); the rest of the height goes to the prompt editor.
    _NEW_DLG_H, _NEW_DLG_CHROME, _NEW_LINE_PX = 720, 320, 19
    new_prompt.expand = False

    def _fit_new_dialog(e=None):
        try: avail = float(page.height or 900) - 80                   # overlay padding top+bottom
        except Exception: avail = 820.0
        h = max(360.0, min(float(_NEW_DLG_H), avail))
        lines = max(4, int((h - _NEW_DLG_CHROME) / _NEW_LINE_PX))
        new_dlg_box.height = h
        new_prompt.min_lines = new_prompt.max_lines = lines
        if new_overlay.visible: page.update()

    new_overlay = ft.Container(
        content=(new_dlg_box := ft.Container(
            content=ft.Column([
                _panel_header(ft.Icons.ADD_PHOTO_ALTERNATE_OUTLINED, "Create new image",
                              C["accent"], close_new),
                ft.Container(
                    content=ft.Column([
                        ft.ResponsiveRow([
                            ft.Column([_section("Workflow"), new_api_dd], col={"xs": 12, "md": 5}, spacing=4),
                            ft.Column([_section("Image size"), new_size_dd], col={"xs": 8, "md": 4}, spacing=4),
                            ft.Column([_section("Steps"), new_steps_tf], col={"xs": 4, "md": 3}, spacing=4),
                        ], spacing=8, run_spacing=8),
                        ft.Row([
                            _section("Prompt"),
                            ft.Container(expand=True),
                            _hist_btn(new_prompt, "new"),
                            ft.IconButton(
                                icon=ft.Icons.BOOKMARK_OUTLINED,
                                visible=False,          # hidden for now (Generate already saves the prompt); behaviour kept
                                icon_color=C["accent"], icon_size=16,
                                tooltip="Save Prompt",
                                on_click=lambda e: (_add_ph((new_prompt.value or "").strip(), dialog="new"),
                                    show_snack("✓ Prompt saved")) if (new_prompt.value or "").strip() else None,
                            ),
                        ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=4),
                        new_prompt,
                    ], spacing=8, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
                    expand=True, padding=po(left=20, right=20, top=16, bottom=8),
                ),
                _panel_actions(
                    new_status,
                    ft.Container(expand=True),
                    ft.TextButton("Cancel", on_click=close_new,
                                 style=ft.ButtonStyle(color=C["text2"])),
                    ft.Row([
                        ft.Text("Quantity:", size=11, color=C["text3"]),
                        _batch_dd_new,
                    ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.FilledButton("✓ Generate", on_click=do_new, bgcolor=C["accent"]),
                ),
            ], spacing=0, expand=True),
            bgcolor=C["surface"], border_radius=br(12),
            width=760, height=_NEW_DLG_H,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )),
        bgcolor="#00000044", expand=True,
        alignment=ft.Alignment(0, 0), visible=False,
        padding=po(left=60, right=60, top=40, bottom=40),
    )
    page.overlay.append(new_overlay)
    _fit_new_dialog()

    # Edit / Mask dialogs use the same compact layout: fixed height (clamped to the window), prompt sized in lines.
    _compact_boxes: list = []         # (box, prompt field, overlay, wanted height, chrome px)

    def _fit_compact_all(e=None):
        try: avail = float(page.height or 900) - 80
        except Exception: avail = 820.0
        for box, prompt, overlay, height, chrome in _compact_boxes:
            h = max(360.0, min(float(height), avail))
            box.height = h
            prompt.min_lines = prompt.max_lines = max(4, int((h - chrome) / _NEW_LINE_PX))
        if any(ov.visible for _b, _p, ov, _h, _c in _compact_boxes): page.update()

    def _make_compact(box, prompt, overlay, height, chrome):
        prompt.expand = False
        _compact_boxes.append((box, prompt, overlay, height, chrome))
        _fit_compact_all()

    # ── Edit overlay ───────────────────────────────────────────────────
    edit_api_dd  = _dlg_dd([ft.dropdown.Option(f) for f in scan_apis()], "Select workflow")
    edit_prompt  = _prompt_editor()
    edit_prompt.hint_text = "Describe the change, for example: Change the color of the shirt to red. Keep the face and everything else unchanged."      # shown while the box is empty
    edit_api_dd.expand = True                 # fill the row like the prompt below it
    edit_src_lbl = ft.Text("", size=10, color=C["text3"], italic=True)
    edit_status  = ft.Text("", size=11, color=C["red"], italic=True)

    def close_edit(e=None):
        edit_overlay.visible = False; page.update()

    async def do_edit(e):
        api = edit_api_dd.value; prompt = (edit_prompt.value or "").strip()
        if not api:   edit_status.value = "⚠ Select a workflow first"; page.update(); return
        if not prompt: edit_status.value = "⚠ Enter a prompt first"; page.update(); return
        close_edit()

        # If there are pending batch items → batch mode
        if _pending_batch_items:
            items_to_edit = list(_pending_batch_items)
            _pending_batch_items.clear()
            await _do_batch_edit_selected_with(items_to_edit, api, prompt)
            return

        item = cur()
        if not item: return
        n = _batch_dd_edit.get_value()
        _batch_size[0] = n
        print(f"[BATCH] do_edit n={n}")
        _add_ph(prompt, dialog="edit")
        _gen_count[0] += 1
        src = item["path"] if Path(item["path"]).exists() else None
        for i in range(n):
            resolved_prompt = resolve_wildcards(prompt)
            _show_progress(resolved_prompt, f"⏳ Edit {i+1}/{n}...")
            def _p(t): _update_progress(t)
            try:
                result = await run_comfy(api, resolved_prompt, img_path=src, on_progress=_p)
                _hide_progress()
                if result:
                    ni = {"id": uuid.uuid4().hex, "path": result, "prompt": resolved_prompt, "api": api,
                          "ts": time.time(), "parent_id": item["id"], "is_temp": True, "gen": _gen_count[0]}
                    _add_ph(ni.get("prompt",""), ni.get("path"), dialog="edit")
                    history.append(ni); current_id[0] = ni["id"]
                    _mark_dirty(); load_image(ni); rebuild_history()
                else: show_snack(f"⚠ Image {i+1} failed", C["red"]); break
            except Exception as ex:
                _hide_progress(); show_snack(f"⚠ {ex}", C["red"]); break
        show_snack(f"✓ Edit completed {n} image(s)" if n > 1 else "✓ Edit completed")

    def _do_save_edit_prompt(e=None):
        p = (edit_prompt.value or "").strip()
        if p: _add_ph(p, dialog="edit"); show_snack("✓ Prompt saved")

    edit_overlay = ft.Container(
        content=(edit_box := ft.Container(
            content=ft.Column([
                _panel_header(ft.Icons.EDIT_OUTLINED, "Edit image", C["amber"], close_edit),
                ft.Container(
                    content=ft.Column([
                        edit_src_lbl,
                        _section("Workflow"), ft.Row([edit_api_dd]),
                        ft.Row([
                            _section("Prompt"),
                            ft.Container(expand=True),
                            _hist_btn(edit_prompt, "edit"),
                            ft.IconButton(
                                icon=ft.Icons.BOOKMARK_OUTLINED,
                                icon_color=C["amber"], icon_size=16,
                                tooltip="Save Prompt",
                                on_click=_do_save_edit_prompt,
                            ),
                        ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=4),
                        edit_prompt,
                        ft.Row([edit_status], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ], spacing=8, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
                    expand=True, padding=po(left=20, right=20, top=16, bottom=8),
                ),
                _panel_actions(
                    ft.TextButton("Cancel", on_click=close_edit,
                                 style=ft.ButtonStyle(color=C["text2"])),
                    ft.Row([
                        ft.Text("Quantity:", size=11, color=C["text3"]),
                        _batch_dd_edit,
                    ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.FilledButton("✓ Edit", on_click=do_edit, bgcolor=C["amber"]),
                ),
            ], spacing=0, expand=True),
            bgcolor=C["surface"], border_radius=br(12),
            width=640, height=560,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )),
        bgcolor="#00000044", expand=True,
        alignment=ft.Alignment(0, 0), visible=False,
        padding=po(left=60, right=60, top=40, bottom=40),
    )
    page.overlay.append(edit_overlay)
    _make_compact(edit_box, edit_prompt, edit_overlay, 560, 312)

    # ── Working Folder ───────────────────────────────────────────────────
    def _wf() -> Path | None:
        return Path(wf_dir[0]) if wf_dir[0] else None

    def _ws_needed() -> bool:
        if _arg_mode or wf_dir[0]: return True
        show_snack("Open a Working Folder first", C["amber"]); return False

    def _ws_guard(fn):
        def _w(*a, **k):
            if not _ws_needed(): return None
            return fn(*a, **k)
        return _w

    def _gen_active() -> bool:
        return bool(prog_overlay.visible or _ACTIVE_PIDS)

    def _refresh_tools():
        on = _arg_mode or bool(wf_dir[0])
        for c in _ws_tools:
            c.disabled = not on; c.opacity = 1.0 if on else 0.4

    def _mask_dir() -> Path:
        """Where mask helper files go: the workspace's masks/ folder, else the ephemeral temp."""
        f = _wf()
        if f is None: return TEMP_DIR
        d = ws_path(f, MASKS_DIRNAME); d.mkdir(parents=True, exist_ok=True); return d

    _pick_overlays: list = []             # the small "choose a workflow" dialogs (Prompt, Scale & Export)

    def _close_modals():
        for ov in (new_overlay, edit_overlay, enhance_overlay, extend_overlay, mask_inpaint_overlay, fs_overlay, *_pick_overlays):
            ov.visible = False
        mask_draw_dlg.open = False; hist_sel_dlg.open = False
        _mask_cancel()

    # ── Row 1: the folder chip + its menu ──
    folder_icon = ft.Icon(ft.Icons.FOLDER_OUTLINED, size=15, color=C["text3"])
    folder_name_lbl = ft.Text("No Working Folder", size=12, color=C["text3"], max_lines=1,
                              overflow=ft.TextOverflow.ELLIPSIS, weight=ft.FontWeight.W_500)
    folder_caret = ft.Icon(ft.Icons.ARROW_DROP_DOWN_ROUNDED, size=18, color=C["text3"])
    folder_chip = ft.Container(
        content=ft.Row([folder_icon, folder_name_lbl, folder_caret], spacing=4, tight=True,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
        padding=po(left=8, right=2, top=3, bottom=3), border_radius=br(6), ink=True,
        tooltip="Choose a Working Folder with Open Folder")
    folder_menu = ft.PopupMenuButton(content=folder_chip, items=[], menu_position=ft.PopupMenuPosition.UNDER)

    def _open_in_explorer(e=None):
        f = _wf()
        if f is None: return
        try: os.startfile(str(f))
        except Exception as ex: show_snack(f"⚠ {ex}", C["red"])

    def _rebuild_folder_menu():
        f = _wf()
        cur_norm = os.path.normcase(os.path.abspath(str(f))) if f else ""
        lbl = lambda t: ft.PopupMenuItem(content=ft.Text(t, size=10, color=C["text3"], weight=ft.FontWeight.W_700),
                                         disabled=True, height=28)
        items = [lbl("Working Folder")]
        if f:
            items.append(ft.PopupMenuItem(content=ft.Column([
                ft.Text(f.name or str(f), size=12, color=C["text1"], weight=ft.FontWeight.W_600),
                ft.Text(str(f), size=10, color=C["text3"]),
            ], spacing=1, tight=True), disabled=True, height=46))
            items.append(ft.PopupMenuItem(content=ft.Text("Open in Explorer", size=12, color=C["text1"]),
                                          icon=ft.Icons.FOLDER_OPEN_ROUNDED, on_click=_open_in_explorer))
        else:
            items.append(ft.PopupMenuItem(content=ft.Text("None selected", size=12, color=C["text3"]),
                                          disabled=True, height=32))
        recents = [r for r in load_editor_config().get("recent_working_folders", [])
                   if isinstance(r, str) and Path(r).is_dir()
                   and os.path.normcase(os.path.abspath(r)) != cur_norm]
        if recents:
            items.append(ft.PopupMenuItem())
            items.append(lbl("Recent"))
            for r in recents[:6]:
                items.append(ft.PopupMenuItem(
                    content=ft.Text(Path(r).name or r, size=12, color=C["text1"]), icon=ft.Icons.FOLDER_OUTLINED,
                    on_click=lambda e, _r=r: _open_working_folder(_r)))
        folder_menu.items = items

    def _refresh_folder_ui():
        f = _wf()
        folder_name_lbl.value = (f.name or str(f)) if f else "No Working Folder"
        folder_name_lbl.color = C["text1"] if f else C["text3"]
        folder_icon.color = C["accent"] if f else C["text3"]
        folder_caret.color = C["text2"] if f else C["text3"]
        folder_chip.tooltip = str(f) if f else "No Working Folder — use Open Folder"
        _rebuild_folder_menu(); _refresh_tools(); _refresh_empty_hint(); _update_title()

    def _open_working_folder(path: str, startup: bool = False) -> bool:
        """Switch the workspace: save the current one, reset everything that belongs to it, then load the
        new folder (history from workspace.json reconciled with the images that actually exist)."""
        folder = Path(path)
        if not folder.is_dir():
            show_snack(f"⚠ Not a folder: {path}", C["red"]); return False
        if not startup and _gen_active():
            show_snack("⚠ A generation is running — stop it before switching folders", C["amber"]); return False
        if wf_dir[0]: _autosave()
        _close_modals()
        history.clear(); _favorites.clear(); _selected_ids.clear(); _pending_batch_items.clear()
        THUMB_CACHE.clear(); current_id[0] = ""
        ws = read_workspace(folder)
        history.extend(ws["history"]); _ignored[:] = ws["ignored"]
        current_id[0] = ws["current_id"] or (history[-1]["id"] if history else "")
        wf_dir[0] = str(folder)
        for sub in (VERSIONS_DIRNAME, MASKS_DIRNAME):
            try: ws_path(folder, sub).mkdir(parents=True, exist_ok=True)
            except OSError: pass
        _cur_temp[0] = ws_path(folder, VERSIONS_DIRNAME)
        remember_working_folder(folder)
        _autosave()                                      # make the reconciliation durable
        _refresh_folder_ui()
        load_image(cur()); rebuild_history()
        if not startup:
            note = f"{len(history)} image(s)"
            if ws["new_roots"] and ws["had_meta"]: note += f", {ws['new_roots']} new"
            if ws["pruned"]: note += f", {ws['pruned']} missing dropped"
            if ws["corrupt"]: note += " — workspace data was unreadable, source images recovered"
            show_snack(f"✓ {folder.name or folder}: {note}")
        return True

    async def _pick_folder(e=None):
        if _gen_active():
            show_snack("⚠ A generation is running — stop it before switching folders", C["amber"]); return
        fp = ft.FilePicker()
        result = await fp.get_directory_path(dialog_title="Open Working Folder")
        if result: _open_working_folder(result)

    # ── Import: images from outside are COPIED into <folder>/Imported ──
    def _import_paths(paths) -> int:
        folder = _wf()
        added, last = 0, None
        for f in paths:
            q = Path(f)
            if q.suffix.lower() not in IMAGE_EXTS or not q.is_file(): continue
            if folder is None:                               # argument mode: edit in place, as before
                item = {"id": uuid.uuid4().hex, "path": str(q), "prompt": "(imported)", "api": "",
                        "ts": time.time(), "parent_id": None, "is_temp": False}
                history.append(item); last = item; added += 1
                continue
            rel = rel_in_folder(folder, q)
            if rel is not None and not rel.lower().startswith(WS_DIRNAME.lower() + "/"):
                dest = q                                     # already in the folder: index in place
            else:
                d = folder / IMPORTED_DIRNAME; d.mkdir(parents=True, exist_ok=True)
                dest = unique_dest(d, q.name)
                try: shutil.copy2(q, dest)
                except OSError as ex:
                    show_snack(f"⚠ Could not import {q.name}: {ex}", C["red"]); continue
            key = (rel_in_folder(folder, dest) or "").lower()
            _ignored[:] = [x for x in _ignored if x.lower() != key]
            known = next((h for h in history if (rel_in_folder(folder, h["path"]) or "").lower() == key), None)
            if known is not None:
                last = known; continue
            item = {"id": uuid.uuid4().hex, "path": str(dest), "prompt": "(imported)", "api": "",
                    "ts": time.time(), "parent_id": None, "is_temp": False,
                    "source": derive_source(folder, dest)}
            history.append(item); last = item; added += 1
        if last is not None:
            current_id[0] = last["id"]
            _mark_dirty(); load_image(last); rebuild_history()
        return added

    # ── Delete: sources are only removed from History; generated versions are deleted with their file ──
    def _children_of(item: dict) -> list:
        return [h for h in history if h.get("parent_id") == item["id"]]

    def _delete_items(items: list):
        ids = {i["id"] for i in items}
        by_id = {h["id"]: h for h in history}

        def _surviving_parent(pid):
            while pid in ids:
                pid = (by_id.get(pid) or {}).get("parent_id")
            return pid
        for h in history:                                    # children keep a valid ancestor (Process Again)
            if h["id"] not in ids and h.get("parent_id") in ids:
                h["parent_id"] = _surviving_parent(h["parent_id"])
        f = _wf()
        removed_sources = 0
        for it in items:
            if it not in history: continue
            history.remove(it); _favorites.discard(it["id"]); _selected_ids.discard(it["id"])
            if f is None: continue                           # argument mode: history only, nothing on disk touched
            if _is_generated(it) and (rel_in_folder(f, it["path"]) or "").lower().startswith(
                    f"{WS_DIRNAME}/{VERSIONS_DIRNAME}/".lower()):
                try: Path(it["path"]).unlink(missing_ok=True)
                except OSError as ex: print(f"could not delete {it['path']}: {ex}")
                THUMB_CACHE.pop(it["path"], None)
            else:
                rel = rel_in_folder(f, it["path"])
                if rel and rel not in _ignored: _ignored.append(rel)
                removed_sources += 1
        prev = next((h for h in reversed(history)), None)
        if current_id[0] not in {h["id"] for h in history}:
            current_id[0] = prev["id"] if prev else ""
        _mark_dirty(); load_image(cur()); rebuild_history()
        return removed_sources

    def _request_delete(items: list, clear_all: bool = False):
        items = [i for i in items if i]
        if not items: return
        if _arg_mode:                                        # argument mode keeps its original-file protection
            items = [i for i in items if i["path"] not in _arg_originals]
            if not items: show_snack("Cannot delete original file", C["red"]); return
        gens = [i for i in items if _is_generated(i)]
        n_src = len(items) - len(gens)

        def _do():
            _delete_items(items)
            msg = f"Deleted {len(gens)} version(s)" if gens else "Removed from History"
            if gens and n_src: msg += f", removed {n_src} source(s) from History"
            if n_src and not _arg_mode: msg += " (source files stay in the folder)"
            show_snack(msg, C["red"] if gens else C["green"])

        if not gens or _arg_mode:                            # nothing irreversible: no confirmation needed
            _do(); return
        what = "all history" if clear_all else (f"{len(gens)} generated version(s)")
        dlg = ft.AlertDialog(
            modal=True, bgcolor=C["surface"],
            title=ft.Text("Delete versions?", size=15, weight=ft.FontWeight.W_700, color=C["text1"]),
            content=ft.Container(width=380, content=ft.Text(
                f"This deletes {what} and the generated file(s) from the workspace."
                + (f"\n{n_src} source image(s) are only removed from History; the files stay in the folder." if n_src else ""),
                size=12, color=C["text2"])),
            actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog(), style=ft.ButtonStyle(color=C["text2"])),
                     ft.FilledButton("Delete", bgcolor=C["red"], on_click=lambda e: (page.pop_dialog(), _do()))],
            actions_alignment=ft.MainAxisAlignment.END)
        page.show_dialog(dlg)

    # ══════════════════════════════════════════════════════════════════
    # POLYGONAL MASK SYSTEM (Stack-based, no Canvas API needed)
    # ══════════════════════════════════════════════════════════════════
    mask_points: list[tuple[float,float]] = []
    _mask_active: list[bool] = [False]
    _viewer_size: list[tuple[float,float]] = [(0,0)]
    _viewer_offset: list[tuple[float,float]] = [(0,0)]  # viewer offset from the edge

    # overlay stack placed over the viewer
    mask_overlay_stack = ft.Stack(expand=True, visible=False)

    mask_hint = ft.Text("", size=11, color="#FFD700")
    mask_status_bar = ft.Container(
        content=ft.Row([
            ft.Icon(ft.Icons.GESTURE_ROUNDED, color="#FFD700", size=14),
            mask_hint,
            ft.Container(expand=True),
            ft.TextButton("Clear points",
                style=ft.ButtonStyle(color=C["text3"]),
                on_click=lambda e: _mask_clear()),
            ft.OutlinedButton(
                content=ft.Row([ft.Icon(ft.Icons.FOLDER_OPEN_OUTLINED,size=13,color="#8B5CF6"),
                                ft.Text("Import Mask PNG",size=11,color="#8B5CF6")],
                               tight=True,spacing=4),
                on_click=lambda e: asyncio.create_task(_import_mask_file()),
                style=ft.ButtonStyle(side=ft.BorderSide(1,"#8B5CF6")),
                height=30,
            ),
            ft.FilledButton("✓ Apply Mask", bgcolor="#8B5CF6", height=30,
                on_click=lambda e: asyncio.create_task(_open_mask_inpaint())),
            ft.TextButton("Cancel",
                style=ft.ButtonStyle(color=C["text2"]),
                on_click=lambda e: _mask_cancel()),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor="#1A1A1A",
        padding=po(left=14,right=14,top=8,bottom=8),
        border=ft.Border(top=ft.BorderSide(2,"#8B5CF6")),
        visible=False,
    )

    _imported_mask_path: list[str] = []  # path of the imported mask

    async def _import_mask_file():
        """Import mask PNG from file instead of drawing a polygon"""
        fp = ft.FilePicker()
        files = await fp.pick_files(
            dialog_title="Select Mask PNG (white=editable area, black=preserve)",
            allow_multiple=False,
            allowed_extensions=["png","jpg","jpeg"],
        )
        if files and files[0].path:
            _imported_mask_path.clear()
            _imported_mask_path.append(files[0].path)
            mask_hint.value = f"✓ Import mask: {Path(files[0].path).name}"
            show_snack("✓ Mask loaded; click Apply Mask to send")
            page.update()

    def _mask_clear():
        mask_points.clear(); _draw_mask(); page.update()

    def _mask_cancel():
        # Do not clear mask_points — keep them for the next use
        _mask_active[0]=False
        _imported_mask_path.clear()
        mask_overlay_stack.visible=False
        mask_status_bar.visible=False; page.update()

    def _dot(x,y,color="#FFD700",size=10):
        return ft.Container(
            width=size, height=size, bgcolor=color,
            border_radius=br(size//2),
            border=ft.Border(
                top=ft.BorderSide(1.5,"#000"),right=ft.BorderSide(1.5,"#000"),
                bottom=ft.BorderSide(1.5,"#000"),left=ft.BorderSide(1.5,"#000"),
            ),
            left=x-size//2, top=y-size//2,
        )

    def _draw_mask():
        """Rebuild overlay stack from mask_points"""
        controls = []
        pts = mask_points

        # draw lines between points
        for i in range(len(pts)):
            x1,y1 = pts[i]
            x2,y2 = pts[(i+1) % len(pts)] if i < len(pts)-1 else (pts[0] if len(pts)>=3 else (x1,y1))
            if i < len(pts)-1 or len(pts) >= 3:
                # Calculate the line as a narrow container rotated with rotate
                import math
                dx,dy = x2-x1, y2-y1
                length = math.sqrt(dx*dx+dy*dy)
                if length > 0:
                    angle = math.degrees(math.atan2(dy,dx))
                    mx,my = (x1+x2)/2, (y1+y2)/2
                    alpha = "66" if (i==len(pts)-1 and len(pts)>=3) else "CC"
                    controls.append(ft.Container(
                        width=length, height=2,
                        bgcolor=f"#FFD700{alpha}",
                        left=mx-length/2, top=my-1,
                        rotate=ft.Rotate(angle=math.radians(angle),
                                        alignment=ft.Alignment(0,0)),
                    ))

        # draw dots (first dot bigger / different color)
        for i,(x,y) in enumerate(pts):
            color = "#FF4444" if i==0 else "#FFD700"
            size  = 14 if i==0 else 10
            controls.append(_dot(x,y,color,size))

        mask_overlay_stack.controls = controls

        n = len(pts)
        if n == 0:    mask_hint.value = "Click to place the first point"
        elif n < 3:   mask_hint.value = f"{n} point(s) — at least 3 points required"
        else:         mask_hint.value = f"{n} point(s) — click the first point (red) to close or press ✓ Apply Mask"

    def _on_mask_tap(e: ft.TapEvent):
        if not _mask_active[0]: return
        lx = e.local_position.x if e.local_position else 0
        ly = e.local_position.y if e.local_position else 0
        gx = e.global_position.x if e.global_position else lx
        gy = e.global_position.y if e.global_position else ly
        # offset = global - local = viewer position on screen
        ox = gx - lx
        oy = gy - ly
        _viewer_offset[0] = (ox, oy)
        print(f"[MASK TAP] local=({lx:.1f},{ly:.1f}) global=({gx:.1f},{gy:.1f}) offset=({ox:.1f},{oy:.1f})")
        # Check whether the first point was tapped → close the polygon
        if mask_points and len(mask_points) >= 3:
            fx, fy = mask_points[0]
            if abs(lx-fx) < 16 and abs(ly-fy) < 16:
                asyncio.create_task(_open_mask_inpaint()); return
        mask_points.append((lx, ly))
        _draw_mask(); page.update()

    def _get_viewer_size() -> tuple[float,float]:
        """Return the actual displayed image size in the viewer (excluding black areas)"""
        item = cur()
        if not item: return (800, 600)
        try:
            from PIL import Image as PI
            im = PI.open(item["path"]); iw, ih = im.size
        except: return (800, 600)
        # viewer area = page width minus left padding (image centered)
        # Based on the screenshot, the image starts around x=370 from the left edge of the page
        # and height = page height - header(44) - toolbar(44) - status(44) - history(160)
        page_w = page.width or 1920
        page_h = (page.height or 1080)
        viewer_h = page_h - 44 - 44 - 44 - 160  # header + toolbar + statusbar + history
        viewer_w = page_w

        # The image is fitted in the viewer while preserving aspect ratio
        scale = min(viewer_w / iw, viewer_h / ih)
        disp_w = iw * scale
        disp_h = ih * scale
        return (disp_w, disp_h)

    mask_gesture = ft.GestureDetector(
        content=ft.Stack([
            ft.Container(expand=True, bgcolor="transparent"),
            mask_overlay_stack,
        ], expand=True),
        on_tap=_on_mask_tap,
        expand=True, visible=False,
    )

    # ── Mask Dialog — image sized to fit, no offset ──────────────────────────
    _mask_dlg_img_size: list[tuple[int,int,int,int]] = [(0,0,0,0)]   # (dw, dh, ox, oy)
    _drag_idx: list[int] = [-1]   # index of the point currently being dragged
    _drag_moved: list[bool] = [False]  # prevent tap after drag
    mask_dlg_stack = ft.Stack()
    mask_dlg_img   = ft.Image(src="", fit=ft.BoxFit.CONTAIN)

    def _img_coords(lx, ly):
        dw, dh, ox, oy = _mask_dlg_img_size[0]
        return lx - ox, ly - oy

    def _nearest_point(ix, iy, radius=20):
        best, best_d = -1, radius*radius
        for i,(x,y) in enumerate(mask_points):
            d = (ix-x)**2 + (iy-y)**2
            if d < best_d: best, best_d = i, d
        return best

    def _mask_dlg_tap(e: ft.TapEvent):
        if not _mask_active[0]: return
        if _drag_moved[0]: _drag_moved[0] = False; return  # end drag without adding a point
        lx = e.local_position.x if e.local_position else 0
        ly = e.local_position.y if e.local_position else 0
        ix, iy = _img_coords(lx, ly)
        dw, dh = _mask_dlg_img_size[0][:2]
        if ix < 0 or ix >= dw or iy < 0 or iy >= dh: return
        if mask_points and len(mask_points) >= 3:
            fx, fy = mask_points[0]
            if abs(ix-fx) < 18 and abs(iy-fy) < 18:
                asyncio.create_task(_open_mask_inpaint()); return
        mask_points.append((ix, iy))
        _draw_mask_on_dialog(); page.update()

    def _mask_dlg_pan_start(e: ft.DragStartEvent):
        if not _mask_active[0]: return
        lx = e.local_position.x if e.local_position else 0
        ly = e.local_position.y if e.local_position else 0
        ix, iy = _img_coords(lx, ly)
        _drag_idx[0] = _nearest_point(ix, iy)
        _drag_moved[0] = False

    def _mask_dlg_pan_update(e: ft.DragUpdateEvent):
        if not _mask_active[0] or _drag_idx[0] < 0: return
        lx = e.local_position.x if e.local_position else 0
        ly = e.local_position.y if e.local_position else 0
        ix, iy = _img_coords(lx, ly)
        dw, dh = _mask_dlg_img_size[0][:2]
        ix = max(0, min(ix, dw-1)); iy = max(0, min(iy, dh-1))
        mask_points[_drag_idx[0]] = (ix, iy)
        _drag_moved[0] = True
        _draw_mask_on_dialog(); page.update()

    def _mask_dlg_pan_end(e: ft.DragEndEvent):
        _drag_idx[0] = -1

    def _draw_mask_on_dialog():
        import math
        pts = mask_points
        print(f"[DRAW] mask_points={len(pts)} stack_id={id(mask_dlg_stack)}")
        controls = [ft.Container(expand=True, bgcolor="transparent")]
        
        # Draw lines
        for i in range(len(pts)):
            x1, y1 = pts[i]
            if i < len(pts) - 1:
                x2, y2 = pts[i+1]
                dx, dy = x2 - x1, y2 - y1
                ln = math.sqrt(dx*dx + dy*dy)
                if ln > 0:
                    angle = math.degrees(math.atan2(dy, dx))
                    mx, my = (x1+x2)/2, (y1+y2)/2
                    controls.append(ft.Container(
                        width=ln, height=2, bgcolor="#FFD700CC",
                        left=mx-ln/2, top=my-1,
                        rotate=ft.Rotate(angle=math.radians(angle), alignment=ft.Alignment(0,0))))
        
        # Draw the closing polygon line
        if len(pts) >= 3:
            x1, y1 = pts[-1]
            x2, y2 = pts[0]
            dx, dy = x2 - x1, y2 - y1
            ln = math.sqrt(dx*dx + dy*dy)
            if ln > 0:
                angle = math.degrees(math.atan2(dy, dx))
                mx, my = (x1+x2)/2, (y1+y2)/2
                controls.append(ft.Container(
                    width=ln, height=1, bgcolor="#FFD70066",
                    left=mx-ln/2, top=my-1,
                    rotate=ft.Rotate(angle=math.radians(angle), alignment=ft.Alignment(0,0))))
        
        # Draw points
        for i, (x, y) in enumerate(pts):
            color = "#FF4444" if i == 0 else "#FFD700"
            sz = 14 if i == 0 else 10
            controls.append(ft.Container(
                width=sz, height=sz, bgcolor=color,
                border_radius=br(sz//2),
                border=ft.Border(
                    top=ft.BorderSide(1.5, "#000"),
                    right=ft.BorderSide(1.5, "#000"),
                    bottom=ft.BorderSide(1.5, "#000"),
                    left=ft.BorderSide(1.5, "#000")),
                left=x-sz//2, top=y-sz//2))
        
        mask_dlg_stack.controls = controls
        n = len(pts)
        if n == 0:
            mask_dlg_hint.value = "Click to place the first point"
        elif n < 3:
            mask_dlg_hint.value = f"{n} point(s) — at least 3 points required"
        else:
            mask_dlg_hint.value = f"{n} point(s) — click the first point (red) to close or press ✓ Apply"

    mask_dlg_hint = ft.Text("Click to place the first point", size=11, color="#FFD700")

    mask_dlg_gesture = ft.GestureDetector(
        content=ft.Container(
            content=ft.Stack([mask_dlg_img, mask_dlg_stack]),
            alignment=ft.Alignment(0,0),
            expand=True,
        ),
        on_tap=_mask_dlg_tap,
        on_pan_start=_mask_dlg_pan_start,
        on_pan_update=_mask_dlg_pan_update,
        on_pan_end=_mask_dlg_pan_end,
        expand=True,
    )

    def close_mask_draw_dlg(e=None):
        mask_draw_dlg.open=False; page.update()

    def _mask_undo(e=None):
        if mask_points: mask_points.pop()
        _draw_mask_on_dialog(); page.update()

    # invert checkbox is in the dialog (moved from the inpaint overlay)
    _mask_dlg_invert_chk = ft.Checkbox(
        label="Invert Mask",
        value=False,
        fill_color={ft.ControlState.SELECTED: "#8B5CF6"},
        check_color="#111827",
        label_style=ft.TextStyle(size=12, color=C["text2"]),
        on_change=lambda e: (_mask_invert.__setitem__(0, e.control.value), page.update()),
    )

    mask_draw_dlg = ft.AlertDialog(
        modal=True,
        title=ft.Row([
            ft.Icon(ft.Icons.GESTURE_ROUNDED, color="#8B5CF6", size=16),
            ft.Text("Draw Mask", size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
            ft.Container(expand=True),
            mask_dlg_hint,
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        content=ft.Container(
            content=mask_dlg_gesture,
            width=800, height=520,
            bgcolor=C["bg"],
            border_radius=br(8),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            padding=0,
            margin=0,
        ),
        content_padding=po(left=0, right=0, top=0, bottom=0),
        bgcolor=C["surface"],
        actions=[
            ft.Container(
                content=ft.Row([
                    _mask_dlg_invert_chk,
                    ft.Container(expand=True),
                    ft.TextButton("Undo",
                        on_click=_mask_undo,
                        style=ft.ButtonStyle(color=C["amber"]),
                    ),
                    ft.TextButton("Clear points",
                        on_click=lambda e: (mask_points.clear(), _draw_mask_on_dialog(), page.update()),
                        style=ft.ButtonStyle(color=C["text3"]),
                    ),
                    ft.TextButton("Cancel",
                        on_click=lambda e: close_mask_draw_dlg(),
                        style=ft.ButtonStyle(color=C["text2"]),
                    ),
                    ft.FilledButton("✓ Apply Mask", bgcolor="#8B5CF6",
                        on_click=lambda e: (close_mask_draw_dlg(),
                            asyncio.create_task(_open_mask_inpaint()))),
                ], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                width=780, padding=po(left=8, right=8, top=0, bottom=0),
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.START,
    )
    page.overlay.append(mask_draw_dlg)

    async def _start_mask_mode(e=None):
        item = cur()
        if not item:
            show_snack("⚠ An image is required before drawing a Mask", C["red"]); return
        # Do not clear mask_points — load the previous mask
        _imported_mask_path.clear()
        _mask_active[0] = True
        # sync invert checkbox
        _mask_dlg_invert_chk.value = _mask_invert[0]

        # Calculate the actual display size based on the aspect ratio within 800x520
        try:
            from PIL import Image as PI
            im = PI.open(item["path"]); iw,ih = im.size
        except: iw,ih = 800,520
        dw,dh,ox,oy = _get_mask_display(iw,ih)
        _mask_dlg_img_size[0] = (int(dw), int(dh), int(ox), int(oy))

        # Set image: single width/height, no expand, and left/top aligned with the offset
        mask_dlg_img.src = _b64(item["path"])
        mask_dlg_img.width = int(dw)
        mask_dlg_img.height = int(dh)
        mask_dlg_img.left = int(ox)
        mask_dlg_img.top = int(oy)
        mask_dlg_img.fit = ft.BoxFit.FILL
        
        # Set the overlay stack: same size as the image and aligned with it
        mask_dlg_stack.width = int(dw)
        mask_dlg_stack.height = int(dh)
        mask_dlg_stack.left = int(ox)
        mask_dlg_stack.top = int(oy)
        mask_dlg_stack.controls = [ft.Container(expand=True, bgcolor="transparent")]

        mask_dlg_hint.value = f"🖼️ {iw}×{ih}"
        mask_draw_dlg.open = True
        page.update()
        await asyncio.sleep(0.1)
        if mask_points:
            _draw_mask_on_dialog()
            page.update()

    # ── Export mask PNG ────────────────────────────────────────────────
    MASK_DLG_W = 800
    MASK_DLG_H = 520   # actual content area excluding title+actions padding

    def _get_mask_display(iw, ih):
        ir = iw/ih; vr = MASK_DLG_W/MASK_DLG_H
        if ir > vr:
            dw=MASK_DLG_W; dh=MASK_DLG_W/ir; ox=0; oy=(MASK_DLG_H-dh)/2
        else:
            dh=MASK_DLG_H; dw=MASK_DLG_H*ir; ox=(MASK_DLG_W-dw)/2; oy=0
        return dw,dh,ox,oy

    def _export_mask_png(img_w,img_h,view_w=None,view_h=None,invert=False)->str:
        from PIL import Image, ImageDraw
        # Read dw,dh from state (ox, oy not needed here because the mask_points coordinates have already been adjusted)
        if len(_mask_dlg_img_size[0]) >= 2:
            dw, dh = _mask_dlg_img_size[0][:2]
        else:
            dw, dh = 800, 520
        
        # mask_points are coordinates on the displayed area (0,0 = top-left of the displayed image at size dw×dh)
        # convert them into coordinates for the original image size
        sx = img_w / dw; sy = img_h / dh
        print(f"[MASK] img={img_w}x{img_h} display={dw:.1f}x{dh:.1f} scale=({sx:.3f},{sy:.3f})")
        scaled = [(max(0,min(int(x*sx),img_w-1)),
                   max(0,min(int(y*sy),img_h-1)))
                  for x,y in mask_points]
        print(f"[MASK] raw={mask_points[:2]} → scaled={scaled[:2]}")
        
        # Create a grayscale mask image: 0=normal, 255=mask area
        mask_L = Image.new("L",(img_w,img_h),0)
        if len(scaled)>=3: ImageDraw.Draw(mask_L).polygon(scaled,fill=255)
        
        # Save debug PNG (grayscale)
        debug = TEMP_DIR/f"mask_debug_{uuid.uuid4().hex[:4]}.png"
        mask_L.save(debug)
        print(f"[MASK] debug (L) saved: {debug}")
        
        # workflow interpret: alpha=0 = inpaint area, alpha=255 = preserve
        # Therefore invert mask_L always, and if the user checks Invert, invert again (= return to normal)
        from PIL import ImageChops
        mask_L = ImageChops.invert(mask_L)
        if invert:
            mask_L = ImageChops.invert(mask_L)
        print(f"[MASK] invert_checkbox={invert}")

        # Create an RGBA mask by placing mask_L into the alpha channel
        # workflow uses LoadImageMask channel="alpha" + InpaintCropImproved mask_invert=false
        # → alpha=255 = editable area, alpha=0 = preserve
        dst = _mask_dir()/f"mask_{uuid.uuid4().hex[:8]}.png"
        mask_rgba = Image.new("RGBA",(img_w,img_h),(0,0,0,0))
        mask_rgba.putalpha(mask_L)
        mask_rgba.save(dst)
        print(f"[MASK] mask (RGBA alpha, invert={invert}) saved: {dst}")
        return str(dst)

    # ── Mask inpaint overlay ───────────────────────────────────────────
    mask_api_dd   = _dlg_dd([ft.dropdown.Option(f) for f in scan_mask_apis()],
                             "Select workflow")
    mask_prompt_f = _prompt_editor()
    mask_api_dd.expand = True
    mask_status_l = ft.Text("",size=11,color=C["red"],italic=True)
    _mask_invert: list[bool] = [False]
    def _on_invert_change(e):
        _mask_invert[0] = e.control.value
        page.update()
    mask_invert_chk = ft.Checkbox(
        label="Invert Mask — inpaint outside the painted area",
        value=False,
        fill_color={ft.ControlState.SELECTED: "#8B5CF6"},
        check_color="#111827",
        label_style=ft.TextStyle(size=12, color=C["text2"]),
        on_change=_on_invert_change,
    )

    def close_mask_dlg(e=None):
        mask_inpaint_overlay.visible=False; page.update()

    async def do_mask_inpaint(e):
        item=cur()
        api=mask_api_dd.value; prompt=(mask_prompt_f.value or "").strip()
        if not api:    mask_status_l.value="⚠ Select a workflow"; page.update(); return
        if not prompt: mask_status_l.value="⚠ Enter a prompt"; page.update(); return

        # Use the imported mask if available; otherwise use the polygon
        if _imported_mask_path:
            mask_path = _imported_mask_path[0]
        else:
            if len(mask_points)<3:
                mask_status_l.value="⚠ Draw the polygon with at least 3 points or press Import Mask PNG"
                page.update(); return
            try:
                from PIL import Image as PI
                im=PI.open(item["path"]); iw,ih=im.size
            except: iw,ih=512,512
            mask_path=_export_mask_png(iw,ih,invert=_mask_invert[0])
        # reset invert checkbox
        _mask_invert[0]=False; _mask_dlg_invert_chk.value=False
        close_mask_dlg(); _mask_active[0]=False
        n = _batch_dd_mask.get_value()
        _batch_size[0] = n
        print(f"[BATCH] do_mask n={n}")
        _add_ph(prompt, dialog="mask")
        _gen_count[0] += 1
        for i in range(n):
            _show_progress(prompt, f"⏳ Inpainting {i+1}/{n}...")
            def _p(t): _update_progress(t)
            try:
                result=await run_comfy(api,prompt,img_path=item["path"],
                                       mask_path=mask_path,on_progress=_p)
                _hide_progress()
                if result:
                    ni={"id":uuid.uuid4().hex,"path":result,
                        "prompt":f"[MASK] {prompt}","api":api,
                        "ts":time.time(),"parent_id":item["id"],"is_temp":True,"gen":_gen_count[0]}
                    _add_ph(prompt, ni.get("path"), dialog="mask")
                    history.append(ni); current_id[0]=ni["id"]
                    _mark_dirty(); load_image(ni); rebuild_history()
                else: show_snack(f"⚠ Image {i+1} failed",C["red"]); break
            except Exception as ex:
                _hide_progress(); show_snack(f"⚠ {ex}",C["red"]); break
        show_snack(f"✓ Inpaint completed {n} image(s)" if n > 1 else "✓ Inpaint completed")

    enh_restore_chk=ft.Checkbox(label="Restore Face (CodeFormer)",value=True,
        fill_color={ft.ControlState.SELECTED:"#8B5CF6"},check_color="#111827",
        label_style=ft.TextStyle(size=13,color=C["text1"]))
    enh_upscale_chk=ft.Checkbox(label="Upscale",value=False,
        fill_color={ft.ControlState.SELECTED:"#8B5CF6"},check_color="#111827",
        label_style=ft.TextStyle(size=13,color=C["text1"]))
    enh_status=ft.Text("",size=11,color=C["red"],italic=True)
    enh_info_lbl=ft.Text("",size=11,color=C["text3"],italic=True)          # which image / how many are going to be enhanced
    enh_status.visible=False
    def _enh_msg(text):
        enh_status.value=text; enh_status.visible=bool(text); page.update()
    enh_api_dd=_dlg_dd([ft.dropdown.Option(f) for f in scan_face_upscale_apis()],"Select workflow")
    _enh_dd_sel=enh_api_dd.on_select
    def _enh_api_changed(e):
        if _enh_dd_sel: _enh_dd_sel(e)
        _last_api["enhance"]=enh_api_dd.value
    enh_api_dd.on_select=_enh_api_changed
    enh_api_dd.expand=True

    def _default_enhance_api(need=("FaceRestoreCFWithModel","ImageUpscaleWithModel")):
        """The workflow to use without asking: the one chosen last, else face_restore_upscale.json, else the first workflow with a node of the kind that is needed."""
        opts=[f for f in scan_face_upscale_apis() if _enhance_nodes_of(f)&set(need)]
        if _last_api["enhance"] in opts: return _last_api["enhance"]
        if "face_restore_upscale.json" in opts: return "face_restore_upscale.json"
        return opts[0] if opts else None

    def _show_enhance_dialog():
        opts=scan_face_upscale_apis()
        enh_api_dd.options=[ft.dropdown.Option(f) for f in opts]
        enh_api_dd.value=_default_enhance_api()
        enh_status.value="" if opts else "⚠ No Face Restore / Upscale workflow found in comfy_apis/enhance"
        enh_status.visible=bool(enh_status.value)
        enh_restore_chk.value=True; enh_upscale_chk.value=False                  # the same defaults every time
        _cur_item=cur()
        enh_info_lbl.value=(f"{len(_pending_enhance_items)} selected images" if _pending_enhance_items
                            else (Path(_cur_item["path"]).name if _cur_item else ""))
        enhance_overlay.visible=True; page.update()
    _pending_enhance_items: list = []

    def close_enhance(e=None):
        _pending_enhance_items.clear()
        enhance_overlay.visible=False; page.update()

    async def do_enhance(e):
        api=enh_api_dd.value
        if not api: _enh_msg("⚠ Select a workflow first"); return
        if not (ENHANCE_DIR/api).exists(): _enh_msg(f"⚠ Not found: {api}"); return
        restore=enh_restore_chk.value; upscale=enh_upscale_chk.value
        if not restore and not upscale: _enh_msg("⚠ Select at least one option"); return
        close_enhance()

        # batch mode if there are pending items
        if _pending_enhance_items:
            items_to_run = list(_pending_enhance_items)
            _pending_enhance_items.clear()
            asyncio.create_task(_do_batch_enhance(items_to_run, api, restore, upscale))
            return

        # single mode
        item=cur()
        if not item: return
        _gen_count[0]+=1
        _show_progress("",f"⏳ Enhance...")
        def _p(t): _update_progress(t)
        try:
            result=await run_enhance(api,item["path"],restore_face=restore,upscale=upscale,on_progress=_p)
            _hide_progress()
            if result:
                ni={"id":uuid.uuid4().hex,"path":result,"prompt":f"[Enhance] face={restore} up={upscale}",
                    "api":api,"ts":time.time(),"parent_id":item["id"],"is_temp":True,"gen":_gen_count[0]}
                _add_ph(ni.get("prompt",""), ni.get("path"))
                history.append(ni); current_id[0]=ni["id"]
                _mark_dirty(); load_image(ni); rebuild_history()
                show_snack("✓ Enhance completed")
            else: show_snack("⚠ No result",C["red"])
        except Exception as ex:
            import traceback; traceback.print_exc()
            _hide_progress(); show_snack(f"⚠ {ex}",C["red"])

    def _enh_option(chk, title_hint):
        return ft.Container(
            content=ft.Column([chk, ft.Container(content=ft.Text(title_hint, size=11, color=C["text3"]), padding=po(left=34))], spacing=0),
            bgcolor="#F5F3FF", border_radius=br(10), border=ft.Border(top=ft.BorderSide(1, "#E4DDFB"), bottom=ft.BorderSide(1, "#E4DDFB"), left=ft.BorderSide(1, "#E4DDFB"), right=ft.BorderSide(1, "#E4DDFB")),
            padding=po(left=14, right=14, top=8, bottom=10),
        )

    enhance_overlay=ft.Container(
        content=ft.Container(
            content=ft.Column([
                _panel_header(ft.Icons.AUTO_FIX_HIGH_ROUNDED,"Enhance image","#8B5CF6",close_enhance),
                ft.Container(
                    content=ft.Column([
                        enh_info_lbl,
                        ft.Text("Workflow", size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
                        ft.Row([enh_api_dd]),
                        enh_status,
                        ft.Container(height=4),
                        ft.Text("Options", size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
                        _enh_option(enh_restore_chk, "Repair and sharpen faces with CodeFormer"),
                        _enh_option(enh_upscale_chk, "Enlarge the picture with the upscale model"),
                    ], spacing=10, tight=True),
                    padding=po(left=20,right=20,top=16,bottom=18),
                ),
                _panel_actions(
                    ft.TextButton("Cancel",on_click=close_enhance,style=ft.ButtonStyle(color=C["text2"])),
                    ft.FilledButton("✓ Enhance",on_click=do_enhance,bgcolor="#8B5CF6"),
                ),
            ],spacing=0,tight=True),
            bgcolor=C["surface"],border_radius=br(12),width=480,
            border=ft.Border(top=ft.BorderSide(1,C["border"]),bottom=ft.BorderSide(1,C["border"]),left=ft.BorderSide(1,C["border"]),right=ft.BorderSide(1,C["border"])),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        ),
        bgcolor="#00000055",expand=True,alignment=ft.Alignment(0,0),
        visible=False,padding=po(left=40,right=40,top=40,bottom=40),
    )
    page.overlay.append(enhance_overlay)

    def _make_pick_dialog(icon, title, color, ok_label, controls, on_ok):
        """A small modal that fits its content: header, the given controls, Cancel / OK. Returns (overlay, close)."""
        def _close(e=None):
            ov.visible = False; page.update()
        card = ft.Container(
            content=ft.Column([
                _panel_header(icon, title, color, _close),
                ft.Container(content=ft.Column(controls, spacing=10, tight=True), padding=po(left=20, right=20, top=16, bottom=18)),
                _panel_actions(
                    ft.TextButton("Cancel", on_click=_close, style=ft.ButtonStyle(color=C["text2"])),
                    ft.FilledButton(ok_label, on_click=on_ok, bgcolor=color),
                ),
            ], spacing=0, tight=True),
            bgcolor=C["surface"], border_radius=br(12), width=480,
            border=ft.Border(top=ft.BorderSide(1, C["border"]), bottom=ft.BorderSide(1, C["border"]), left=ft.BorderSide(1, C["border"]), right=ft.BorderSide(1, C["border"])),
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )
        ov = ft.Container(content=card, bgcolor="#00000055", expand=True, alignment=ft.Alignment(0, 0), visible=False, padding=po(left=40, right=40, top=40, bottom=40))
        page.overlay.append(ov); _pick_overlays.append(ov)
        return ov, _close

    def _wf_dropdown(key):
        """A Workflow dropdown that remembers the last choice in _last_api[key]."""
        dd = _dlg_dd([], "Select workflow")
        _sel = dd.on_select
        def _changed(e):
            if _sel: _sel(e)
            _last_api[key] = dd.value
        dd.on_select = _changed
        dd.expand = True
        return dd

    # ── Image to prompt: which caption workflow ──
    i2p_info = ft.Text("", size=11, color=C["text3"], italic=True)
    i2p_dd = _wf_dropdown("caption")
    i2p_status = ft.Text("", size=11, color=C["red"], italic=True, visible=False)
    _i2p_item: list = [None]

    def _i2p_ok(e):
        api = i2p_dd.value
        if not api:
            i2p_status.value = "⚠ Select a workflow first"; i2p_status.visible = True; page.update(); return
        _close_i2p()
        asyncio.create_task(_run_img2prompt(_i2p_item[0], api))

    i2p_overlay, _close_i2p = _make_pick_dialog(ft.Icons.AUTO_AWESOME_ROUNDED, "Image to prompt", "#EC4899", "✓ Analyze", [
        i2p_info,
        ft.Text("Workflow", size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
        ft.Row([i2p_dd]), i2p_status,
    ], _i2p_ok)

    def _open_img2prompt(e=None):
        item = cur()
        if not item: show_snack("⚠ An image is required first", C["red"]); return
        # the list: every workflow in comfy_apis/caption (joycaption ones first); the one used last is selected
        all_apis = sorted(p.name for p in CAPTION_DIR.glob("*.json"))
        apis = sorted(all_apis, key=lambda x: (0 if "joycaption" in x.lower() else 1))
        if not apis:
            show_snack("⚠ Florence2/Image2Prompt workflow not found", C["red"]); return
        i2p_dd.options = [ft.dropdown.Option(f) for f in apis]
        i2p_dd.value = _last_api["caption"] if _last_api["caption"] in apis else apis[0]
        i2p_info.value = Path(item["path"]).name
        i2p_status.visible = False; _i2p_item[0] = item
        i2p_overlay.visible = True; page.update()

    async def _run_img2prompt(item, api):
        _show_progress("", "⏳ Analyzing image...")
        try:
            result = await run_image2prompt(api, item["path"])
            _hide_progress()
            if result:
                # Show the prompt in a dialog so it can be copied or inserted into the prompt field
                _show_prompt_result_dlg(result)
            else:
                show_snack("⚠ No result from ComfyUI", C["red"])
        except Exception as ex:
            import traceback; traceback.print_exc()
            _hide_progress(); show_snack(f"⚠ {ex}", C["red"])

    async def _auto_generate_from_prompt(prompt: str):
        """Generate a new image from the prompt immediately — using the workflow and API from new_api_dd"""
        api = new_api_dd.value
        if not api:
            apis = scan_create_apis()
            if not apis: show_snack("⚠ Workflow not found", C["red"]); return
            api = apis[0]
        resolved = resolve_wildcards(prompt)
        _gen_count[0] += 1
        _show_progress(resolved, "⏳ Generate...")
        def _p(t): _update_progress(t)
        try:
            result = await run_comfy(api, resolved, on_progress=_p)
            _hide_progress()
            if result:
                ni = {"id": uuid.uuid4().hex, "path": result, "prompt": resolved,
                      "api": api, "ts": time.time(), "parent_id": None,
                      "is_temp": True, "gen": _gen_count[0]}
                _add_ph(ni.get("prompt",""), ni.get("path"))
                history.append(ni); current_id[0] = ni["id"]
                _mark_dirty(); load_image(ni); rebuild_history()
                show_snack("✓ Generate complete")
            else: show_snack("⚠ No result", C["red"])
        except Exception as ex:
            import traceback; traceback.print_exc()
            _hide_progress(); show_snack(f"⚠ {ex}", C["red"])

    def _show_prompt_result_dlg(prompt_text: str):
        prompt_display = ft.TextField(
            value=prompt_text, multiline=True, min_lines=6, max_lines=None,
            read_only=False, expand=True,
            text_style=ft.TextStyle(font_family="Consolas", size=12, color="#111827"),
            bgcolor="#FFFFFF", border_color="#D1D5DB",
            focused_border_color="#2563EB",
            content_padding=po(left=12, right=12, top=10, bottom=10),
        )
        dlg_ref: list = [None]

        def _close(e=None):
            dlg_ref[0].open = False
            page.update()

        def _use_as_new(e):
            new_prompt.value = prompt_display.value
            _add_ph(prompt_display.value.strip(), dialog="new")
            _close()
            _open_new(None)

        def _save_to_hist(e):
            p = (prompt_display.value or "").strip()
            if p: _add_ph(p, dialog="new"); show_snack("✓ Prompt saved")

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([
                ft.Icon(ft.Icons.AUTO_AWESOME_ROUNDED, color="#8B5CF6", size=16),
                ft.Text("Image → Prompt", size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
                ft.Container(expand=True),
                ft.IconButton(ft.Icons.CLOSE_ROUNDED, icon_color=C["text2"], icon_size=18,
                             on_click=_close, width=30, height=30, padding=0),
            ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            content=ft.Container(
                content=ft.Column([
                    ft.Text("Prompt extracted from the image — you can edit it before using it",
                            size=10, color=C["text3"], italic=True),
                    ft.Container(content=prompt_display, expand=True),
                ], spacing=8, expand=True),
                width=600, height=360, bgcolor=C["panel"],
                border_radius=br(8), padding=po(left=12, right=12, top=12, bottom=12),
            ),
            bgcolor=C["surface"],
            actions=[
                ft.Container(
                    content=ft.Row([
                        ft.TextButton("💾 Save to History", on_click=_save_to_hist,
                                     style=ft.ButtonStyle(color=C["text2"])),
                        ft.Container(expand=True),
                        ft.TextButton("Close", on_click=_close,
                                     style=ft.ButtonStyle(color=C["text3"])),
                        ft.FilledButton("→ New", on_click=_use_as_new, bgcolor=C["accent"]),
                    ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    expand=True, padding=po(left=4, right=4, top=0, bottom=0),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.START,
        )
        dlg_ref[0] = dlg
        page.overlay.append(dlg)
        dlg.open = True
        page.update()

    # ── Extend (Outpaint) Dialog ──────────────────────────────────────
    _ext_vals: dict = {"left":0,"top":0,"right":0,"bottom":0,"feathering":150}
    _ext_preview_img = ft.Image(src="", fit=ft.BoxFit.CONTAIN)
    _ext_status = ft.Text("", size=11, color=C["red"], italic=True)
    _ext_img_size: list = [1, 1]  # [w, h] of the actual image
    ext_size_lbl = ft.Text("", size=11, color=C["text2"], italic=True)
    ext_dims_lbl = ft.Text("", size=11, color=C["text2"], text_align=ft.TextAlign.RIGHT, expand=True, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)    # Original / Extended sizes above the preview
    _ext_canvas_size: list = [660, 320]          # [w, h] of the preview area, set by _fit_extend_dialog

    def _update_ext_label():
        """What the current resize option will produce (same arithmetic as do_extend)."""
        iw, ih = _ext_img_size
        L, R, T, B = _ext_vals["left"], _ext_vals["right"], _ext_vals["top"], _ext_vals["bottom"]
        sel = _ext_resize[0]
        color = C["text2"]
        if sel == 0 and _ext_ratio_lock[0]:
            plan = auto_extend_plan(iw, ih, L, R, T, B, _ext_vals["feathering"])
            if plan is None:
                text = "Auto: set an extension above to see the result size."
            else:
                pctv = round(plan["scale"] * 100)
                text = f"Auto \u2192 result {plan['out_w']}\u00d7{plan['out_h']} (the size of the original); the original picture is reduced to {pctv}% ({plan['src_w']}\u00d7{plan['src_h']})."
                if plan["scale"] < 0.4:
                    text += " That is a lot: the existing part loses detail. A smaller extension (or Enhance afterwards) keeps more."
                    color = C["amber"]
        else:
            pctv = 100 if sel == 0 else sel
            nw, nh = (max(64, int(iw * pctv / 100)), max(64, int(ih * pctv / 100))) if pctv < 100 else (iw, ih)
            ow = nw + int(L * pctv / 100) + int(R * pctv / 100)
            oh = nh + int(T * pctv / 100) + int(B * pctv / 100)
            text = f"{pctv}% \u2192 result {ow}\u00d7{oh} (original {iw}\u00d7{ih})."
        ext_size_lbl.value = text
        ext_size_lbl.color = color

    # Canvas preview — shows the image and the colored overlay that will be expanded
    _ext_canvas = ft.Stack(clip_behavior=ft.ClipBehavior.HARD_EDGE)

    def _update_ext_preview():
        """Draw a preview that simulates the extension"""
        iw, ih = _ext_img_size
        L = _ext_vals["left"]; R = _ext_vals["right"]
        T = _ext_vals["top"];  B = _ext_vals["bottom"]
        total_w = iw + L + R; total_h = ih + T + B
        _update_ext_label()
        if total_w <= 0 or total_h <= 0: return

        CW, CH = _ext_canvas_size
        scale = min(CW/total_w, CH/total_h)
        disp_w = total_w * scale; disp_h = total_h * scale
        ox = (CW - disp_w) / 2;  oy = (CH - disp_h) / 2

        # Position of the actual image on the canvas
        img_x = ox + L * scale; img_y = oy + T * scale
        img_w = iw * scale;     img_h = ih * scale
        lw = L * scale; rw = R * scale
        th = T * scale; bh = B * scale

        controls = [
            # Full canvas background
            ft.Container(width=CW, height=CH, bgcolor="#2B333B"),
        ]

        # Left expansion area
        if L > 0:
            controls.append(ft.Container(
                left=ox, top=img_y, width=lw, height=img_h,
                gradient=ft.LinearGradient(
                    begin=ft.Alignment(-1,0), end=ft.Alignment(1,0),
                    colors=["#DCFCE7", "#BBF7D088"]
                ),
                border=ft.Border(
                    top=ft.BorderSide(1,"#16A34A44"),
                    bottom=ft.BorderSide(1,"#16A34A44"),
                    left=ft.BorderSide(1,"#16A34A66"),
                ),
                content=ft.Text(f"+{L}px", size=11, color="#15803D",
                    text_align=ft.TextAlign.CENTER),
                alignment=ft.Alignment(0,0),
            ))
        # Right expansion area
        if R > 0:
            controls.append(ft.Container(
                left=img_x+img_w, top=img_y, width=rw, height=img_h,
                gradient=ft.LinearGradient(
                    begin=ft.Alignment(-1,0), end=ft.Alignment(1,0),
                    colors=["#BBF7D088", "#DCFCE7"]
                ),
                border=ft.Border(
                    top=ft.BorderSide(1,"#16A34A44"),
                    bottom=ft.BorderSide(1,"#16A34A44"),
                    right=ft.BorderSide(1,"#16A34A66"),
                ),
                content=ft.Text(f"+{R}px", size=11, color="#15803D",
                    text_align=ft.TextAlign.CENTER),
                alignment=ft.Alignment(0,0),
            ))
        # Top expansion area
        if T > 0:
            controls.append(ft.Container(
                left=ox, top=oy, width=disp_w, height=th,
                gradient=ft.LinearGradient(
                    begin=ft.Alignment(0,-1), end=ft.Alignment(0,1),
                    colors=["#DCFCE7", "#BBF7D088"]
                ),
                border=ft.Border(top=ft.BorderSide(1,"#16A34A66")),
                content=ft.Text(f"+{T}px", size=11, color="#15803D",
                    text_align=ft.TextAlign.CENTER),
                alignment=ft.Alignment(0,0),
            ))
        # Bottom expansion area
        if B > 0:
            controls.append(ft.Container(
                left=ox, top=img_y+img_h, width=disp_w, height=bh,
                gradient=ft.LinearGradient(
                    begin=ft.Alignment(0,-1), end=ft.Alignment(0,1),
                    colors=["#BBF7D088", "#DCFCE7"]
                ),
                border=ft.Border(bottom=ft.BorderSide(1,"#16A34A66")),
                content=ft.Text(f"+{B}px", size=11, color="#15803D",
                    text_align=ft.TextAlign.CENTER),
                alignment=ft.Alignment(0,0),
            ))

        # Actual image
        controls.append(ft.Container(
            content=_ext_preview_img,
            left=img_x, top=img_y, width=img_w, height=img_h,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        ))
        # Actual image border (dashed style)
        controls.append(ft.Container(
            left=img_x, top=img_y, width=img_w, height=img_h,
            border=ft.Border(
                top=ft.BorderSide(1,"#11182788"), bottom=ft.BorderSide(1,"#11182788"),
                left=ft.BorderSide(1,"#11182788"), right=ft.BorderSide(1,"#11182788"),
            ),
        ))
        # sizes: the extended canvas that ComfyUI will round to a multiple of 8 (header above the preview)
        real_w = (total_w // 8) * 8
        real_h = (total_h // 8) * 8
        ext_dims_lbl.value = f"Original {iw}\u00d7{ih} px   \u00b7   Extended {int(real_w)}\u00d7{int(real_h)} px (input {int(total_w)}\u00d7{int(total_h)})   \u00b7   L {L}  R {R}  T {T}  B {B}"

        _ext_canvas.controls = controls
        page.update()

    _ext_lock: list[bool] = [False]            # "Lock all 4 sides equally" (its checkbox is hidden; the logic stays)
    _ext_ratio_lock: list[bool] = [True]       # default: lock the original aspect ratio (Auto resize needs it)
    _ext_slider_refs: dict = {}   # key → (slider, val_lbl)

    def _mk_slider_ctrl(label, key, max_val=500):
        val_lbl = ft.Text(str(_ext_vals[key]), size=12, color="#06B6D4",
                          width=36, text_align=ft.TextAlign.RIGHT,
                          weight=ft.FontWeight.W_600)
        slider = ft.CupertinoSlider(
            value=float(_ext_vals[key]),
            min=0, max=float(max_val), divisions=max_val,
            active_color="#06B6D4",
            expand=True,
        )
        _ext_slider_refs[key] = (slider, val_lbl)
        def _on_change(e):
            v = int(round(e.control.value))
            _ext_vals[key] = v
            val_lbl.value = str(v)
            iw, ih = _ext_img_size
            if key != "feathering":
                if _ext_lock[0]:
                    # Lock all 4 sides equally
                    for k in ("left","right","top","bottom"):
                        if k != key:
                            _ext_vals[k] = v
                            s, l = _ext_slider_refs.get(k, (None,None))
                            if s: s.value = float(v)
                            if l: l.value = str(v)
                elif _ext_ratio_lock[0] and iw > 0 and ih > 0:
                    # Lock aspect ratio: calculate the opposite side
                    ratio = iw / ih
                    if key in ("left","right"):
                        # Change L or R → calculate T and B
                        total_h_pad = int((_ext_vals["left"] + _ext_vals["right"]) / ratio)
                        half_v = total_h_pad // 2
                        for k, val in [("top", half_v), ("bottom", total_h_pad - half_v)]:
                            _ext_vals[k] = val
                            s, l = _ext_slider_refs.get(k, (None,None))
                            if s: s.value = min(float(val), 500.0)
                            if l: l.value = str(val)
                    else:
                        # Change T or B → calculate L and R
                        total_w_pad = int((_ext_vals["top"] + _ext_vals["bottom"]) * ratio)
                        half_h = total_w_pad // 2
                        for k, val in [("left", half_h), ("right", total_w_pad - half_h)]:
                            _ext_vals[k] = val
                            s, l = _ext_slider_refs.get(k, (None,None))
                            if s: s.value = min(float(val), 500.0)
                            if l: l.value = str(val)
            _update_ext_label()
            page.update()
        def _on_end(e):
            _update_ext_preview()
        slider.on_change = _on_change
        slider.on_change_end = _on_end
        return ft.Row([
            ft.Text(label, size=11, color=C["text2"], width=72),
            slider,
            val_lbl,
            ft.Text("px", size=10, color=C["text3"], width=18),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    ext_api_dd = _dlg_dd([ft.dropdown.Option(f) for f in scan_outpaint_apis()], "Select outpaint workflow")
    def _on_ext_api_sel(e):
        _last_api["extend"] = ext_api_dd.value
    _ext_dd_sel = ext_api_dd.on_select
    def _ext_api_changed(e):
        if _ext_dd_sel: _ext_dd_sel(e)
        _on_ext_api_sel(e)
    ext_api_dd.on_select = _ext_api_changed
    ext_api_dd.expand = True                  # fill the row like the other dialogs

    ext_prompt = ft.TextField(
        value="extend the background, seamless continuation, matching lighting, color, and style of the original image",
        multiline=True, min_lines=3, max_lines=5,
        text_style=ft.TextStyle(font_family="Consolas", size=12, color="#111827"),
        bgcolor="#FFFFFF", border_color="#D1D5DB", focused_border_color="#2563EB",
        content_padding=po(left=10,right=10,top=8,bottom=8),
    )

    _ext_auto_dropped: list[bool] = [False]   # True when Auto was switched to 100% only because the ratio lock was turned off
    _ext_resize: list[int] = [0]  # 0 = Auto (result = size of the original), else 25/50/75/100 (% of the picture and of the extensions)

    def _style_resize_btns():
        sel = _ext_resize[0]
        for rb in _resize_btns:
            rb.style = ft.ButtonStyle(
                color="#06B6D4" if rb.data==sel else C["text2"],
                side=ft.BorderSide(1,"#06B6D4" if rb.data==sel else C["border"]),
                padding=ft.Padding(8,4,8,4),
            )

    def _mk_resize_btn(pct):
        btn = ft.OutlinedButton("Auto" if pct == 0 else f"{pct}%", height=30)
        def _on_click(e, p=pct, b=btn):
            _ext_resize[0] = p
            _ext_auto_dropped[0] = False
            _style_resize_btns()
            _update_ext_label()
            page.update()
        btn.on_click = _on_click
        btn.data = pct
        return btn

    _resize_btns = [_mk_resize_btn(p) for p in [0,25,50,75,100]]
    _style_resize_btns()
    _resize_btns[0].visible = _ext_ratio_lock[0]        # Auto only exists while the aspect ratio is locked
    ext_resize_row = ft.Row([*_resize_btns], spacing=8, run_spacing=8, wrap=True)

    def _sync_auto_option():
        """Auto exists only while the aspect ratio is locked: hide it and fall back to 100% otherwise (and bring it back when the lock returns)."""
        _resize_btns[0].visible = _ext_ratio_lock[0]
        if not _ext_ratio_lock[0] and _ext_resize[0] == 0:
            _ext_resize[0] = 100                  # Auto needs the locked ratio: back to 100%
            _ext_auto_dropped[0] = True
            _style_resize_btns()
        elif _ext_ratio_lock[0] and _ext_auto_dropped[0]:
            _ext_resize[0] = 0                    # the lock is back: Auto again (it was only dropped because of the unlock)
            _ext_auto_dropped[0] = False
            _style_resize_btns()

    def _on_lock_change(e):
        _ext_lock[0] = e.control.value
        if _ext_lock[0]:
            _ext_ratio_lock[0] = False
            ext_ratio_chk.value = False
            _sync_auto_option()
            v = _ext_vals["left"]
            for k in ("right","top","bottom"):
                _ext_vals[k] = v
                s, l = _ext_slider_refs.get(k, (None,None))
                if s: s.value = float(v)
                if l: l.value = str(v)
            _update_ext_preview()
            page.update()

    def _on_ratio_change(e):
        _ext_ratio_lock[0] = e.control.value
        if _ext_ratio_lock[0]:
            _ext_lock[0] = False
            ext_lock_chk.value = False
        _sync_auto_option()
        _update_ext_label()
        page.update()

    ext_lock_chk = ft.Checkbox(
        label="Lock all 4 sides equally", value=False, visible=False,
        fill_color={ft.ControlState.SELECTED: "#06B6D4"},
        check_color="#111827",
        label_style=ft.TextStyle(size=12, color=C["text2"]),
        on_change=_on_lock_change,
    )
    ext_ratio_chk = ft.Checkbox(
        label="Lock original aspect ratio", value=True,
        fill_color={ft.ControlState.SELECTED: "#06B6D4"},
        check_color="#111827",
        label_style=ft.TextStyle(size=12, color=C["text2"]),
        on_change=_on_ratio_change,
    )

    pad_ctrls = ft.Column([
        ft.Column([ext_lock_chk, ext_ratio_chk], spacing=0),
        _mk_slider_ctrl("← Left",   "left"),
        _mk_slider_ctrl("→ Right",  "right"),
        _mk_slider_ctrl("↑ Top",    "top"),
        _mk_slider_ctrl("↓ Bottom", "bottom"),
        _mk_slider_ctrl("Feather",  "feathering"),
    ], spacing=2)

    def close_extend(e=None):
        extend_overlay.visible = False; page.update()

    async def do_extend(e):
        item = cur()
        if not item: _ext_status.value="⚠ An image is required first"; page.update(); return
        api = ext_api_dd.value
        if not api: _ext_status.value="⚠ Select an outpaint workflow first (comfy_apis/enhance)"; page.update(); return
        if not (ENHANCE_DIR/api).exists(): _ext_status.value=f"⚠ Not found: {api}"; page.update(); return
        prompt = (ext_prompt.value or "").strip()
        if not prompt: _ext_status.value="⚠ Enter a prompt first"; page.update(); return
        pct = _ext_resize[0]
        auto_plan = None
        if pct == 0 and _ext_ratio_lock[0]:                         # Auto: the result keeps the size of the original picture
            try:
                from PIL import Image as PI
                _aw, _ah = PI.open(item["path"]).size
            except Exception:
                _aw, _ah = _ext_img_size
            auto_plan = auto_extend_plan(_aw, _ah, _ext_vals["left"], _ext_vals["right"], _ext_vals["top"], _ext_vals["bottom"], _ext_vals["feathering"])
            if auto_plan is None:
                _ext_status.value = "⚠ Set an extension first (Left / Right / Top / Bottom)"; page.update(); return
        elif pct == 0:
            pct = 100                                               # Auto without the locked ratio does not exist: as 100%
        close_extend()
        _gen_count[0] += 1
        # Resize the image first if a value below 100% is selected (Auto: to the size that makes the extended canvas the original size)
        src_path = item["path"]
        if auto_plan:
            try:
                from PIL import Image as PI
                im = PI.open(src_path)
                im_r = im.resize((auto_plan["src_w"], auto_plan["src_h"]), PI.LANCZOS)
                tmp = Path(TEMP_DIR) / f"ext_resize_{uuid.uuid4().hex[:8]}.jpg"
                im_r.save(tmp, quality=92)
                src_path = str(tmp)
                _show_progress(prompt, f"⏳ Extend (Auto: {auto_plan['out_w']}×{auto_plan['out_h']}, picture {round(auto_plan['scale']*100)}%)...")
                print(f"[EXTEND] auto: picture {auto_plan['src_w']}×{auto_plan['src_h']}, result {auto_plan['out_w']}×{auto_plan['out_h']}")
            except Exception as ex:
                print(f"[EXTEND] auto resize failed: {ex}")
                _show_progress(prompt, "⏳ Extend...")
                auto_plan = None
        elif pct < 100:
            try:
                from PIL import Image as PI
                import tempfile
                im = PI.open(src_path)
                nw = max(64, int(im.width * pct / 100))
                nh = max(64, int(im.height * pct / 100))
                im_r = im.resize((nw, nh), PI.LANCZOS)
                tmp = Path(TEMP_DIR) / f"ext_resize_{uuid.uuid4().hex[:8]}.jpg"
                im_r.save(tmp, quality=92)
                src_path = str(tmp)
                _show_progress(prompt, f"⏳ Extend ({pct}% = {nw}×{nh})...")
                print(f"[EXTEND] resized to {pct}% → {nw}×{nh}")
            except Exception as ex:
                print(f"[EXTEND] resize failed: {ex}")
                _show_progress(prompt, "⏳ Extend...")
        else:
            _show_progress(prompt, "⏳ Extend...")
        # Scale the padding values according to the image ratio
        scale_factor = pct / 100
        scaled_left   = max(0, int(_ext_vals["left"]   * scale_factor))
        scaled_right  = max(0, int(_ext_vals["right"]  * scale_factor))
        scaled_top    = max(0, int(_ext_vals["top"]    * scale_factor))
        scaled_bottom = max(0, int(_ext_vals["bottom"] * scale_factor))
        scaled_feather= max(0, int(_ext_vals["feathering"] * scale_factor))
        if auto_plan:                                               # Auto: the extensions that go with the resized picture
            scaled_left, scaled_right, scaled_top, scaled_bottom = auto_plan["left"], auto_plan["right"], auto_plan["top"], auto_plan["bottom"]
            scaled_feather = auto_plan["feather"]
        print(f"[EXTEND] scaled pad: L={scaled_left} R={scaled_right} T={scaled_top} B={scaled_bottom} F={scaled_feather}")
        _opt = "Auto" if auto_plan else f"{pct}%"
        try:
            from PIL import Image as _PI0
            with _PI0.open(item["path"]) as _o: _ow, _oh = _o.size
        except Exception:
            _ow, _oh = _ext_img_size
        _extend_log(f"[EXTEND] REQUEST option={_opt} ratio_lock={_ext_ratio_lock[0]} | original {_ow}x{_oh} | dialog pads L{_ext_vals['left']} R{_ext_vals['right']} T{_ext_vals['top']} B{_ext_vals['bottom']} feather {_ext_vals['feathering']}"
                    f" | planned pads L{scaled_left} R{scaled_right} T{scaled_top} B{scaled_bottom} feather {scaled_feather}")
        def _p(t): _update_progress(t)
        try:
            result = await run_outpaint(
                api, src_path, prompt,
                left=scaled_left, top=scaled_top,
                right=scaled_right, bottom=scaled_bottom,
                feathering=scaled_feather,
                on_progress=_p,
            )
            _hide_progress()
            if result:
                ni={"id":uuid.uuid4().hex,"path":result,"prompt":f"[Extend] {prompt}",
                    "api":api,"ts":time.time(),"parent_id":item["id"],"is_temp":True,"gen":_gen_count[0]}
                _add_ph(ni.get("prompt",""), ni.get("path"))
                history.append(ni); current_id[0]=ni["id"]
                _mark_dirty(); load_image(ni); rebuild_history()
                show_snack("✓ Extend completed")
            else: show_snack("⚠ No result", C["red"])
        except Exception as ex:
            import traceback; traceback.print_exc()
            _hide_progress(); show_snack(f"⚠ {ex}", C["red"])

    # ── Layout: header | preview 70% + controller 30% | footer ─────────────────────────────────────────────
    ext_prompt.expand = True
    def _ext_section(title, *controls):
        return ft.Column([ft.Text(title, size=13, weight=ft.FontWeight.W_700, color=C["text1"]), *controls], spacing=8)

    ext_canvas_box = ft.Container(
        content=_ext_canvas, width=660, height=320, bgcolor="#2B333B", border_radius=br(10),
        clip_behavior=ft.ClipBehavior.HARD_EDGE, alignment=ft.Alignment(0, 0),
    )
    ext_left = ft.Container(
        content=ft.Column([
            ft.Row([ft.Text("Image Preview", size=14, weight=ft.FontWeight.W_700, color=C["text1"]), ext_dims_lbl], vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ext_canvas_box,
            ft.Text("Original image + extended canvas preview", size=11, color=C["text3"]),
        ], spacing=10, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        padding=po(left=20, right=20, top=16, bottom=16), expand=7,
    )
    ext_right_col = ft.Column([
        _ext_section("Canvas Extension", pad_ctrls),
        ft.Divider(height=1, color=C["border"]),
        _ext_section("Generation Settings",
                     ft.Text("Workflow", size=11, color=C["text2"]), ft.Row([ext_api_dd]),
                     ft.Text("Prompt", size=11, color=C["text2"]), ft.Row([ext_prompt])),
        ft.Divider(height=1, color=C["border"]),
        _ext_section("Output Resolution",
                     ft.Text("Resize before sending (saves generation time)", size=11, color=C["text3"], italic=True),
                     ext_resize_row, ext_size_lbl),
        _ext_status,
    ], spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)
    ext_right = ft.Container(
        content=ext_right_col, padding=po(left=20, right=20, top=16, bottom=12), expand=3,
        border=ft.Border(left=ft.BorderSide(1, C["border"])),
    )
    ext_body = ft.Container(expand=True)
    ext_dlg_box = ft.Container(
        content=ft.Column([
            _panel_header(ft.Icons.OPEN_WITH_ROUNDED, "Extend Image", "#06B6D4", close_extend),
            ext_body,
            _panel_actions(
                ft.TextButton("Cancel", on_click=close_extend,
                              style=ft.ButtonStyle(color=C["text2"])),
                ft.FilledButton("✓ Extend", on_click=do_extend, bgcolor="#06B6D4"),
            ),
        ], spacing=0, expand=True),
        bgcolor=C["surface"], border_radius=br(12), width=1100, height=700,
        border=ft.Border(top=ft.BorderSide(1, C["border"]), bottom=ft.BorderSide(1, C["border"]), left=ft.BorderSide(1, C["border"]), right=ft.BorderSide(1, C["border"])),
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )

    def _fit_extend_dialog(e=None):
        """Size the dialog to the window (about 90%, capped for a desktop) and the preview to its pane. Wide window: preview 70% | controller 30%; a narrow one stacks them and scrolls the body."""
        try: pw = float(page.width or 1400); ph = float(page.height or 900)
        except Exception: pw, ph = 1400.0, 900.0
        W = max(560.0, min(pw * 0.9, 1700.0)); H = max(460.0, min(ph * 0.94, 1000.0))
        ext_dlg_box.width = W; ext_dlg_box.height = H
        body_h = H - 124                                           # header + footer
        if W >= 900:
            cw = int(W * 0.7) - 44; ch = int(body_h - 100)
            ext_left.expand = 7; ext_right.expand = 3
            ext_right.height = None
            ext_right_col.scroll = ft.ScrollMode.AUTO; ext_right_col.expand = True
            ext_right.border = ft.Border(left=ft.BorderSide(1, C["border"]))
            ext_body.content = ft.Row([ext_left, ext_right], spacing=0, vertical_alignment=ft.CrossAxisAlignment.STRETCH, expand=True)
        else:
            cw = int(W) - 44; ch = int(max(200, min(360, body_h * 0.4)))
            ext_left.expand = False; ext_right.expand = False
            ext_right_col.scroll = None; ext_right_col.expand = False
            ext_right.border = ft.Border(top=ft.BorderSide(1, C["border"]))
            ext_body.content = ft.Column([ext_left, ext_right], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
        cw = max(240, cw); ch = max(160, ch)
        ext_canvas_box.width = cw; ext_canvas_box.height = ch
        _ext_canvas.width = cw; _ext_canvas.height = ch
        _ext_canvas_size[0] = cw; _ext_canvas_size[1] = ch

    def _on_extend_resize(e=None):
        if extend_overlay.visible:
            _fit_extend_dialog(); _update_ext_preview()

    extend_overlay = ft.Container(
        content=ext_dlg_box,
        bgcolor="#00000055", expand=True,
        alignment=ft.Alignment(0, 0), visible=False,
        padding=po(left=20, right=20, top=20, bottom=20),
    )
    _fit_extend_dialog()
    page.overlay.append(extend_overlay)

    def _open_extend(e=None):
        item = cur()
        if not item: show_snack("⚠ An image is required first", C["red"]); return
        try:
            from PIL import Image as PI
            im = PI.open(item["path"]); iw,ih = im.size
            _ext_img_size[0] = iw; _ext_img_size[1] = ih
            _ext_preview_img.src = _b64(item["path"])
            _ext_preview_img.width = None; _ext_preview_img.height = None
            _ext_preview_img.fit = ft.BoxFit.FILL
        except: pass
        _ext_status.value = ""
        _opts = scan_outpaint_apis()
        ext_api_dd.options = [ft.dropdown.Option(f) for f in _opts]
        if _last_api["extend"] in _opts: ext_api_dd.value = _last_api["extend"]
        elif "flux_fill_outpaint.json" in _opts: ext_api_dd.value = "flux_fill_outpaint.json"
        else: ext_api_dd.value = _opts[0] if _opts else None
        if not _opts: _ext_status.value = "⚠ No outpaint workflow found: put one with a Pad Image for Outpainting node in comfy_apis/enhance"
        extend_overlay.visible = True
        _fit_extend_dialog()
        page.update()
        _update_ext_preview()

    def _open_enhance(e=None):
        """Enhance the current image: the dialog asks for the workflow and the options (Restore Face / Upscale)."""
        item=cur()
        if not item: show_snack("⚠ An image is required first",C["red"]); return
        _pending_enhance_items.clear()
        _show_enhance_dialog()

    # ── Scale & Export: which upscale workflow ──
    scale_info = ft.Text("", size=11, color=C["text3"], italic=True)
    scale_dd = _wf_dropdown("scale")
    scale_status = ft.Text("", size=11, color=C["red"], italic=True, visible=False)
    _pending_scale_items: list = []

    async def _scale_ok(e):
        api = scale_dd.value
        if not api:
            scale_status.value = "⚠ Select a workflow first"; scale_status.visible = True; page.update(); return
        items = list(_pending_scale_items); _pending_scale_items.clear()
        _close_scale()
        await _scale_export_run(items, api)

    scale_overlay, _close_scale = _make_pick_dialog(ft.Icons.OPEN_IN_FULL_ROUNDED, "Scale & Export", "#10B981", "✓ Choose folder & export", [
        scale_info,
        ft.Text("Workflow", size=13, weight=ft.FontWeight.W_700, color=C["text1"]),
        ft.Row([scale_dd]), scale_status,
    ], _scale_ok)

    async def _scale_export(items: list):
        """Scale & Export: the dialog asks for the upscale workflow, then the export runs."""
        if not items: show_snack("⚠ An image is required first", C["red"]); return
        opts = [f for f in scan_face_upscale_apis() if "ImageUpscaleWithModel" in _enhance_nodes_of(f)]
        if not opts: show_snack("⚠ No upscale workflow found in comfy_apis/enhance", C["red"]); return
        scale_dd.options = [ft.dropdown.Option(f) for f in opts]
        scale_dd.value = _last_api["scale"] if _last_api["scale"] in opts else _default_enhance_api(("ImageUpscaleWithModel",))
        _pending_scale_items[:] = items
        scale_info.value = (f"{len(items)} images" if len(items) > 1 else Path(items[0]["path"]).name) + " will be upscaled and saved to a folder you choose"
        scale_status.visible = False
        scale_overlay.visible = True; page.update()

    async def _scale_export_run(items: list, api: str):
        """Upscale and save directly to a folder without showing it in history"""
        # Select a folder first
        fp = ft.FilePicker()
        dst_ref: list = [None]
        async def _pick():
            result = await fp.get_directory_path(dialog_title="Select a folder for Scale Up & Export")
            dst_ref[0] = result
        await _pick()
        if not dst_ref[0]: return
        dst_dir = Path(dst_ref[0])
        import shutil
        total = len(items); saved = 0
        for i, item in enumerate(items):
            _show_progress("", f"⏳ Scale Up {i+1}/{total}: {Path(item['path']).name}")
            def _p(t): _update_progress(t)
            try:
                _prev_tmp = _cur_temp[0]; _cur_temp[0] = TEMP_DIR       # intermediate output stays out of the workspace
                try: result = await run_enhance(api, item["path"], restore_face=False, upscale=True, on_progress=_p)
                finally: _cur_temp[0] = _prev_tmp
                _hide_progress()
                if result:
                    dst = unique_dest(dst_dir, Path(item["path"]).stem + "_4x" + Path(result).suffix)   # re-exports keep the earlier files
                    dst = await _export_util.copy_unique(result, dst)    # in a worker thread: the window stays responsive; never replaces a file (the next free name if taken meanwhile)
                    # Delete the temp file
                    try: Path(result).unlink()
                    except: pass
                    saved += 1
                else: show_snack(f"⚠ Image {i+1} failed", C["red"])
            except Exception as ex:
                import traceback; traceback.print_exc()
                _hide_progress(); show_snack(f"⚠ {ex}", C["red"]); break
        show_snack(f"✓ Scale Up & Export completed {saved}/{total} image(s) → {dst_dir.name}")

    async def _run_single_enhance(item, api):
        _gen_count[0]+=1
        _show_progress("",f"⏳ Restore Face...")
        def _p(t): _update_progress(t)
        try:
            result=await run_enhance(api,item["path"],restore_face=True,upscale=False,on_progress=_p)
            _hide_progress()
            if result:
                ni={"id":uuid.uuid4().hex,"path":result,"prompt":"[Enhance] restore face",
                    "api":api,"ts":time.time(),"parent_id":item["id"],"is_temp":True,"gen":_gen_count[0]}
                _add_ph(ni.get("prompt",""), ni.get("path"))
                history.append(ni); current_id[0]=ni["id"]
                _mark_dirty(); load_image(ni); rebuild_history()
                show_snack("✓ Restore Face completed")
            else: show_snack("⚠ No result",C["red"])
        except Exception as ex:
            import traceback; traceback.print_exc()
            _hide_progress(); show_snack(f"⚠ {ex}",C["red"])

    mask_inpaint_overlay=ft.Container(
        content=(mask_box := ft.Container(
            content=ft.Column([
                _panel_header(ft.Icons.GESTURE_ROUNDED,"Mask Inpainting",
                              "#8B5CF6",close_mask_dlg),
                ft.Container(
                    content=ft.Column([
                        _section("Inpainting Workflow"),ft.Row([mask_api_dd]),mask_status_l,
                        ft.Row([
                            _section("Prompt (the painted mask area will be inpainted)"),
                            ft.Container(expand=True),
                            _hist_btn(mask_prompt_f, "mask"),
                            ft.IconButton(
                                icon=ft.Icons.BOOKMARK_OUTLINED,
                                icon_color="#8B5CF6", icon_size=16,
                                tooltip="Save Prompt",
                                on_click=lambda e: (_add_ph((mask_prompt_f.value or "").strip(), dialog="mask"),
                                    show_snack("✓ Prompt saved")) if (mask_prompt_f.value or "").strip() else None,
                            ),
                        ], vertical_alignment=ft.CrossAxisAlignment.CENTER, spacing=4),
                        mask_prompt_f,
                    ],spacing=8, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.STRETCH),
                    expand=True,padding=po(left=20,right=20,top=16,bottom=8),
                ),
                _panel_actions(
                    ft.TextButton("Cancel",on_click=close_mask_dlg,
                                 style=ft.ButtonStyle(color=C["text2"])),
                    ft.Row([
                        ft.Text("Quantity:", size=11, color=C["text3"]),
                        _batch_dd_mask,
                    ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.FilledButton("✓ Inpaint",on_click=do_mask_inpaint,
                                   bgcolor="#8B5CF6"),
                ),
            ],spacing=0,expand=True),
            bgcolor=C["surface"],border_radius=br(12),
            width=640,height=560,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
        )),
        bgcolor="#00000044",expand=True,
        alignment=ft.Alignment(0,0),visible=False,
        padding=po(left=60,right=60,top=40,bottom=40),
    )
    page.overlay.append(mask_inpaint_overlay)
    _make_compact(mask_box, mask_prompt_f, mask_inpaint_overlay, 560, 312)

    async def _open_mask_inpaint():
        if len(mask_points)<3:
            show_snack("⚠ At least 3 points are required",C["red"]); return
        mask_files = scan_mask_apis()
        all_files  = scan_apis()
        if mask_files:
            mask_api_dd.options = [ft.dropdown.Option(f) for f in mask_files]
            mask_api_dd.hint_text = "Select workflow"
        else:
            mask_api_dd.options = [ft.dropdown.Option(f) for f in all_files]
            mask_api_dd.hint_text = "No compatible inpainting workflow found"
            show_snack("No compatible inpainting workflow found — showing all workflows", C["amber"])
        mask_status_l.value = (
            f"{len(mask_files)} compatible workflow(s) found" if mask_files
            else "No compatible inpainting workflow found"
        )
        mask_status_l.color = C["text3"] if mask_files else C["red"]
        # Load the latest prompt if none is set yet
        if not (mask_prompt_f.value or "").strip():
            last_p = next((
                (e["text"] if isinstance(e,dict) else e)
                for e in prompt_hists["mask"]
                if not (e["text"] if isinstance(e,dict) else e).startswith("[")
            ), "")
            if last_p: mask_prompt_f.value = last_p
        _fit_compact_all()
        mask_inpaint_overlay.visible=True; page.update()

    # ── Toolbar ────────────────────────────────────────────────────────
    def _open_new(e):
        files = scan_create_apis()
        new_api_dd.options = [ft.dropdown.Option(f) for f in files]
        if _last_api["new"]: new_api_dd.value = _last_api["new"]
        last_p = next((
            (e["text"] if isinstance(e,dict) else e)
            for e in prompt_hists["new"]
            if not (e["text"] if isinstance(e,dict) else e).startswith("[")
        ), "")
        if not (new_prompt.value or "").strip(): new_prompt.value = last_p
        new_status.value = ""
        _refresh_steps(_selected_steps())
        _fit_new_dialog()
        new_overlay.visible = True; page.update()

    def _open_edit(e):
        item = cur()
        files = scan_apis()
        edit_api_dd.options = [ft.dropdown.Option(f) for f in files]
        edit_src_lbl.value = f"Edit from: {Path(item['path']).name}" if item else "No image yet"
        edit_prompt.value = ""        # the Edit box always opens empty (the example hint shows); an earlier prompt is one click away in Prompt History
        if item and item.get("api") and item["api"] in [o.key for o in edit_api_dd.options]:
            edit_api_dd.value = item["api"]
        edit_status.value = ""
        _fit_compact_all()
        edit_overlay.visible = True; page.update()

    async def _process_again(e):
        """Re-run with the API + prompt + original source of the current card
        without deleting the existing image — create a new item from the same parent"""
        item=cur()
        if not item:
            show_snack("⚠ No image available to process again",C["red"]); return
        api=item.get("api","")
        prompt=item.get("prompt","")
        if not api or not prompt or prompt=="(imported)":
            show_snack("⚠ Missing API or previous prompt data",C["red"]); return
        if prompt.startswith("["):                          # "[Enhance] ...", "[Extend] ...": made by a tool whose settings are not kept with the picture
            show_snack("⚠ Again works for pictures made with New / Edit. Run Enhance, Extend or Mask again from the original picture.",C["amber"]); return
        # Use the original source of this item (parent if present, otherwise itself)
        if item.get("parent_id"):
            src_item=next((h for h in history if h["id"]==item["parent_id"]),None)
            src=src_item["path"] if src_item and Path(src_item["path"]).exists() else None
        else:
            src=item["path"] if Path(item["path"]).exists() else None
        label="🔄 Process Again..."
        resolved = resolve_wildcards(prompt)
        _show_progress(resolved, label)
        def _p(t): _update_progress(t)
        try:
            result=await run_comfy(api, resolved, img_path=src, on_progress=_p)
            _hide_progress()
            if result:
                ni={"id":uuid.uuid4().hex,"path":result,"prompt":resolved,"api":api,
                    "ts":time.time(),"parent_id":item.get("parent_id"),
                    "is_temp":True}
                # an image made from another image is an Edit result: its prompt belongs to Edit's list, not New's
                _add_ph(ni.get("prompt",""), ni.get("path"), dialog="edit" if item.get("parent_id") else "new")
                history.append(ni); current_id[0]=ni["id"]
                _mark_dirty(); load_image(ni); rebuild_history()
                show_snack("✓ Process Again complete")
            else:
                show_snack("⚠ No image was produced",C["red"])
        except Exception as ex:
            _hide_progress(); show_snack(f"⚠ {ex}",C["red"])

    async def _import(e):
        fp=ft.FilePicker()
        files=await fp.pick_files(dialog_title="Import image",allow_multiple=True,
                    allowed_extensions=["jpg","jpeg","png","webp","bmp"])
        if not files: return
        added=_import_paths([f.path for f in files if f.path])
        if added:
            show_snack(f"✓ Imported {added} image(s)" if added>1 else "✓ Image imported")

    def _delete_cur(e):
        item=cur()
        if item: _request_delete([item])

    # ── Argument-mode Save (overwrite original) ────────────────────────
    def _find_arg_root(item:dict) -> dict|None:
        """Walk parent_id chain to find the root arg-original item."""
        visited = set()
        node = item
        while node:
            if node["path"] in _arg_originals:
                return node
            pid = node.get("parent_id")
            if not pid or pid in visited: break
            visited.add(pid)
            node = next((h for h in history if h["id"] == pid), None)
        return None

    _arg_save_confirm_dlg: list = [None]

    def _arg_save(e=None):
        item = cur()
        if not item: return
        root = _find_arg_root(item)
        if not root:
            show_snack("Cannot find original file", C["red"]); return
        orig_path = root["path"]
        src_path  = item["path"]
        if src_path == orig_path:
            show_snack("No changes to save", C["amber"]); return

        def _do_save(ev):
            _arg_save_confirm_dlg[0].open = False; page.update()
            try:
                shutil.copy2(src_path, orig_path)
                # Update the root item's path to reflect the saved file and reload
                root["path"] = orig_path
                load_image(cur()); rebuild_history()
                show_snack(f"✓ Saved → {Path(orig_path).name}", C["green"])
            except Exception as ex:
                show_snack(f"Save failed: {ex}", C["red"])

        def _cancel(ev):
            _arg_save_confirm_dlg[0].open = False; page.update()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Overwrite original?", size=14, weight=ft.FontWeight.W_700),
            content=ft.Text(f"This will replace:\n{orig_path}", size=12, color=C["text3"]),
            actions=[
                ft.TextButton("Cancel", on_click=_cancel),
                ft.FilledButton("Save", on_click=_do_save,
                                style=ft.ButtonStyle(bgcolor=C["accent"])),
            ],
        )
        _arg_save_confirm_dlg[0] = dlg
        page.overlay.append(dlg)
        dlg.open = True; page.update()

    def _tb(icon,label,color,on_click,tooltip="",small=False,ws=False,prio=3):
        """Toolbar button. ws=True: needs a Working Folder (guarded + dimmed without one).
        prio: how long its text label survives when the window is narrow (higher = kept longer)."""
        sz=10 if small else 11
        lbl=ft.Text(label,size=sz,color=color,weight=ft.FontWeight.W_500)
        _tb_labels.append((lbl,prio))
        btn=ft.Container(
            content=ft.Row([ft.Icon(icon,color=color,size=14),lbl],spacing=4,tight=True),
            on_click=_ws_guard(on_click) if ws else on_click,ink=True,
            padding=po(left=10,right=10,top=6,bottom=6),border_radius=br(5),
            bgcolor=C["panel"],tooltip=tooltip or label,
            border=ft.Border(top=ft.BorderSide(1,C["border"]),right=ft.BorderSide(1,C["border"]),
                             bottom=ft.BorderSide(1,C["border"]),left=ft.BorderSide(1,C["border"])),
        )
        if ws: _ws_tools.append(btn)
        return btn

    def _sep(): return ft.Container(width=1,height=20,bgcolor=C["border"])

    def _zoom_btn(icon,tip,cb):
        return ft.IconButton(icon=icon,icon_color=C["text2"],icon_size=16,on_click=cb,width=28,height=28,
                             padding=0,tooltip=tip)

    _toolbar_left_prefix = (
        [_tb(ft.Icons.SAVE_OUTLINED,"Save",C["green"],_arg_save,"Save — overwrite original file"), _sep()]
        if _arg_mode else
        [_tb(ft.Icons.FOLDER_OPEN_OUTLINED,"Open Folder",C["accent"],lambda e: asyncio.create_task(_pick_folder()),
             "Open a Working Folder (use the folder picker's New Folder to create one)",prio=4), _sep()]
    )
    _toolbar_right_suffix = [_tb(ft.Icons.DELETE_OUTLINE_ROUNDED,"Delete",C["red"],_delete_cur,"Delete this image",
                                 ws=True,prio=1)]

    # Row 2 is ONE row: Open Folder | New Import | Edit … Scale & Export  ····  100% − + Fit Fullscreen | Delete.
    # It never wraps; when the window is narrow the text labels are hidden (tooltips stay) — see _apply_density.
    toolbar=ft.Container(
        content=ft.Row([
            ft.Row([
                *_toolbar_left_prefix,
                *([
                    _tb(ft.Icons.ADD_PHOTO_ALTERNATE_OUTLINED,"New",C["accent"],_open_new,"Create a new image",ws=True,prio=4),
                    _tb(ft.Icons.FILE_UPLOAD_OUTLINED,"Import",C["text2"],lambda e:asyncio.create_task(_import(e)),
                        "Import images into the Working Folder",ws=True,prio=4),
                    _sep(),
                ] if not _arg_mode else []),
                _tb(ft.Icons.EDIT_OUTLINED,"Edit",C["amber"],_open_edit,"Edit the current image",ws=True,prio=4),
                _tb(ft.Icons.REFRESH_ROUNDED,"Again",C["green"],lambda e:asyncio.create_task(_process_again(e)),"Process Again — re-run with the original API and prompt",ws=True,prio=2),
                _tb(ft.Icons.GESTURE_ROUNDED,"Mask","#8B5CF6",lambda e: asyncio.create_task(_start_mask_mode(e)),"Draw a polygonal mask for inpainting",ws=True,prio=2),
                _tb(ft.Icons.AUTO_AWESOME_ROUNDED,"Prompt","#EC4899",_open_img2prompt,"Analyze the image to create a prompt",ws=True,prio=2),
                _tb(ft.Icons.OPEN_WITH_ROUNDED,"Extend","#06B6D4",_open_extend,"Extend/Outpaint the current image",ws=True,prio=2),
                _tb(ft.Icons.AUTO_FIX_HIGH_ROUNDED,"Enhance","#A78BFA",_open_enhance,"Restore Face / Upscale",ws=True,prio=2),
                _tb(ft.Icons.FILE_DOWNLOAD_OUTLINED,"Export",C["green"],lambda e:asyncio.create_task(do_save_as()),
                    "Export — save a copy of this image to a file (Save As)",ws=True,prio=3),
                _tb(ft.Icons.OPEN_IN_NEW_ROUNDED,"Scale & Export","#10B981",
                    lambda e: asyncio.create_task(_scale_export([cur()] if cur() else [])),
                    "Scale up the current image and export it",ws=True,prio=2),
            ],spacing=4,wrap=False),
            ft.Row([
                zoom_lbl_btn,
                _zoom_btn(ft.Icons.ZOOM_OUT_ROUNDED,"Zoom out",zoom_out),
                _zoom_btn(ft.Icons.ZOOM_IN_ROUNDED,"Zoom in",zoom_in),
                _zoom_btn(ft.Icons.FIT_SCREEN_ROUNDED,"Fit",zoom_fit),
                _tb(ft.Icons.FULLSCREEN_ROUNDED,"Fullscreen",C["text2"],open_fullscreen,prio=1),
                _sep(),
                *_toolbar_right_suffix,
            ],spacing=4,wrap=False),
        ],alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
          vertical_alignment=ft.CrossAxisAlignment.CENTER,wrap=False),
        bgcolor=C["toolbar"],padding=po(left=12,right=12,top=7,bottom=7),
        border=ft.Border(bottom=ft.BorderSide(1,C["border"])),
    )

    def _apply_density(e=None):
        """Keep Row 2 on one line: below ~1300px drop the low-priority labels, below ~1120px all tool labels."""
        try: w=float(page.width or page.window.width or 1400)
        except Exception: w=1400.0
        floor = 2 if w < 1120 else (1 if w < 1300 else 0)     # labels with prio <= floor are hidden
        for lbl,prio in _tb_labels:
            lbl.visible = prio > floor
        zoom_lbl_btn.visible = w >= 1120
        try: page.update()
        except Exception: pass

    # ── Images panel ───────────────────────────────────────────────────
    # A one-row filmstrip with Explorer-style selection. The data model is untouched (history / current_id / _selected_ids);
    #   current_id       = the image open in the viewer
    #   _selected_ids    = the batch selection
    #   _sel_anchor      = start of a Shift range
    # (the internal names stay "history"; only the labels the user reads say "Images")
    _CW, _CH, _GAP, _PADX, _PADY = 96, 100, 8, 12, 8        # card width / height, gap, strip padding
    _ACC, _ACC_LT, _ACC_RING, _HOVER = C["accent"], "#EFF6FF", "#93C5FD", "#F3F7FF"
    _sel_anchor: list = [None]
    _select_mode: list = [False]
    _mods = {"ctrl": False, "shift": False}                   # tracked from key down/up (flet's mouse events carry no modifiers)
    _order: list = []                                         # item ids, in display order (newest first)
    _card_ctl: dict = {}                                      # item id -> {"card", "badge", "hover"}
    _strip = {"x": 0.0, "view": 0.0}                          # horizontal scroll offset / panel width
    _mq = {"on": False, "base": set(), "sx": 0.0, "sy": 0.0, "px": 0.0, "py": 0.0, "vx": 0.0, "task": None}

    hist_count_lbl = ft.Text("", size=11, color=C["text3"])
    history_row = ft.Row(scroll=ft.ScrollMode.AUTO, spacing=_GAP, scroll_interval=40,
                         on_scroll=lambda e: _on_strip_scroll(e))

    def _strip_max() -> float:
        n = len(_order)
        total = n * (_CW + _GAP) - _GAP if n else 0
        return max(0.0, total - max(0.0, _strip["view"] - 2 * _PADX))

    def _on_strip_scroll(e):
        if _mq["task"] is None: _strip["x"] = float(e.pixels or 0)

    async def _strip_scroll_to(x: float, smooth: bool = True):
        x = min(_strip_max(), max(0.0, x))
        _strip["x"] = x
        try: await history_row.scroll_to(offset=x, duration=140 if smooth else 0)
        except Exception: pass

    def _scroll_item_into_view(iid):
        """Scroll the filmstrip only when the card is outside the visible part (never resets the position)."""
        if iid not in _order or _strip["view"] <= 0: return
        x0 = _order.index(iid) * (_CW + _GAP)                  # content x inside the row
        x1 = x0 + _CW
        vis0, vis1 = _strip["x"], _strip["x"] + _strip["view"] - 2 * _PADX
        if x0 < vis0: page.run_task(_strip_scroll_to, x0)
        elif x1 > vis1: page.run_task(_strip_scroll_to, x1 - (_strip["view"] - 2 * _PADX))

    # ── card look (updated in place — a selection change never rebuilds the strip) ──
    def _style_card(iid):
        ref = _card_ctl.get(iid)
        if not ref: return
        sel, is_cur = iid in _selected_ids, iid == current_id[0]
        card, badge = ref["card"], ref["badge"]
        card.border = ft.Border.all(2 if (sel or is_cur) else 1, _ACC if sel else (_ACC_RING if is_cur else C["border"]))
        card.bgcolor = _ACC_LT if sel else "#FFFFFF"
        ref["ov"].bgcolor = "#2563EB14" if (ref["hover"] and not sel) else None
        badge.visible = _select_mode[0] or (sel and len(_selected_ids) > 1)
        badge.bgcolor = _ACC if sel else "#FFFFFFD9"
        badge.content.color = "#FFFFFF" if sel else "transparent"

    def _refresh_cards(ids=None):
        for iid in (ids if ids is not None else list(_card_ctl)): _style_card(iid)

    def _sel_items() -> list:
        return [h for h in history if h["id"] in _selected_ids]

    def _set_sel(ids, anchor=None):
        old, new = set(_selected_ids), set(ids)
        if anchor is not None: _sel_anchor[0] = anchor
        if new == old: return False                            # nothing changed: no restyle, no toolbar rebuild, no update
        _selected_ids.clear(); _selected_ids.update(new)
        # only the cards whose state changed are restyled — unless the badge rule flips (it depends on "more than one
        # selected" / Select mode), then every card involved is
        affected = (old | new) if (_select_mode[0] or (len(old) > 1) != (len(new) > 1)) else (old ^ new)
        _refresh_cards(list(affected))
        _refresh_toolbar()
        page.update()
        return True

    def _open_item(iid):
        item = next((h for h in history if h["id"] == iid), None)
        if not item: return
        prev = current_id[0]
        current_id[0] = iid
        load_image(item)
        _refresh_cards([prev, iid])
        _scroll_item_into_view(iid)
        page.update()

    def _card_tap(iid):
        ctrl, shift = _mods["ctrl"], _mods["shift"]
        if shift:                                             # range from the anchor; Ctrl+Shift adds it to the selection
            a = _sel_anchor[0] if _sel_anchor[0] in _order else (current_id[0] if current_id[0] in _order else iid)
            rng = set(_range_ids(_order, a, iid))
            _set_sel((_selected_ids | rng) if ctrl else rng)
        elif ctrl or _select_mode[0]:                         # toggle; the viewer is left alone
            ids = set(_selected_ids); ids ^= {iid}
            _set_sel(ids, anchor=iid)
        else:                                                 # plain click: select just this one and open it
            _set_sel({iid}, anchor=iid)
            _open_item(iid)

    def _clear_sel(e=None):
        if _selected_ids: _set_sel(set())

    def _select_all(e=None):
        _set_sel(set(_order), anchor=(_order[0] if _order else None))

    def _toggle_select_mode(e=None):
        _select_mode[0] = not _select_mode[0]
        _refresh_cards(); _refresh_toolbar(); page.update()

    def _step_image(delta: int):
        """Arrow keys: open the previous / next image of the filmstrip (display order) and select just it."""
        if fs_overlay.visible or _ctx_layer.visible or not _order: return
        i = _order.index(current_id[0]) if current_id[0] in _order else -1
        j = max(0, min(len(_order) - 1, (0 if i < 0 else i + delta)))
        if j == i: return
        iid = _order[j]
        _set_sel({iid}, anchor=iid)
        _open_item(iid)

    # ── keyboard (Ctrl/Shift state, Ctrl+A, Esc). The listener only hears keys while it has focus, so it is
    # re-focused whenever the mouse enters / presses in the panel; modifiers are reset when the window loses focus. ──
    def _on_kb_down(e):
        k = (e.key or "").lower()
        if "control" in k or k.startswith("ctrl"): _mods["ctrl"] = True
        elif "shift" in k: _mods["shift"] = True
        elif k == "a" and _mods["ctrl"]: _select_all()
        elif k in ("arrow left", "arrow right", "arrow up", "arrow down"):
            _step_image(-1 if k in ("arrow left", "arrow up") else 1)
        elif k in ("escape", "esc"):
            if _ctx_layer.visible: _ctx_close()
            elif _mq["on"]: _mq_cancel()
            else: _clear_sel()

    def _on_kb_up(e):
        k = (e.key or "").lower()
        if "control" in k or k.startswith("ctrl"): _mods["ctrl"] = False
        elif "shift" in k: _mods["shift"] = False

    def _focus_panel(e=None):
        try: page.run_task(panel_kb.focus)
        except Exception: pass

    # ── marquee (rubber-band) selection: pointer coordinates are converted to filmstrip CONTENT coordinates
    # (x + scroll offset), so the rectangle and the hit-test stay correct while the strip auto-scrolls ──
    def _mq_apply():
        sx = _strip["x"]
        cx, cy = _mq["px"] + sx, _mq["py"]
        xa, xb = min(_mq["sx"], cx), max(_mq["sx"], cx)
        ya, yb = min(_mq["sy"], cy), max(_mq["sy"], cy)
        left = max(0.0, xa - sx); right = min(_strip["view"], xb - sx)
        mq_rect.left, mq_rect.top = left, max(0.0, ya)
        mq_rect.width, mq_rect.height = max(1.0, right - left), max(1.0, yb - ya)
        mq_rect.visible = True
        hits = {_order[i] for i in _marquee_hits(len(_order), xa, xb, ya, yb, _CW, _GAP, _PADX, _PADY, _CH)}
        # the rectangle moves on every pointer event, but the selection (cards, toolbar, menu) only when the hit set changed
        if not _set_sel((_mq["base"] | hits) if _mq.get("add") else hits):
            mq_rect.update()

    def _mq_down(e):
        """Pointer-down position: the rubber band must start where the button went down, not where the drag was recognised."""
        _mq["dx"], _mq["dy"], _mq["dscroll"] = e.local_position.x, e.local_position.y, _strip["x"]

    def _mq_start(e):
        _focus_panel()
        _mq.update(on=True, add=_mods["ctrl"], base=set(_selected_ids) if _mods["ctrl"] else set(), vx=0.0,
                   sx=_mq.get("dx", e.local_position.x) + _mq.get("dscroll", _strip["x"]), sy=_mq.get("dy", e.local_position.y),
                   px=e.local_position.x, py=e.local_position.y)
        _mq["restore"] = set(_selected_ids)

    def _mq_update(e):
        if not _mq["on"]: return
        _mq["px"], _mq["py"] = e.local_position.x, e.local_position.y
        edge, w = 36.0, _strip["view"]
        _mq["vx"] = (-(edge - _mq["px"]) / edge * 22 if _mq["px"] < edge else
                     ((_mq["px"] - (w - edge)) / edge * 22 if w and _mq["px"] > w - edge else 0.0))
        if _mq["vx"] and _mq["task"] is None:
            _mq["task"] = page.run_task(_mq_autoscroll)
        _mq_apply()

    async def _mq_autoscroll():
        try:
            while _mq["on"] and _mq["vx"]:
                nx = min(_strip_max(), max(0.0, _strip["x"] + _mq["vx"]))
                if nx == _strip["x"]: break
                await _strip_scroll_to(nx, smooth=False)
                _mq_apply()
                await asyncio.sleep(0.03)
        finally:
            _mq["task"] = None

    def _mq_end(e=None):
        _mq["on"] = False; _mq["vx"] = 0.0
        mq_rect.visible = False
        page.update()

    def _mq_cancel():
        _mq["on"] = False; _mq["vx"] = 0.0
        mq_rect.visible = False
        if not _set_sel(_mq.get("restore", set())): page.update()

    def _on_strip_wheel(e):
        """Plain wheel scrolls the filmstrip sideways (Shift+wheel and touchpads already do natively)."""
        if _mods["shift"] or abs(e.scroll_delta.y) <= abs(e.scroll_delta.x): return
        page.run_task(_strip_scroll_to, _strip["x"] + e.scroll_delta.y, False)

    # ── context menu (right click). flet's PopupMenuButton cannot be opened from code and ContextMenu builds its items
    # before the app can react to the click, so this is a small overlay placed at the cursor: it always describes the
    # CURRENT selection. Right-clicking an unselected image selects it first (like Explorer). ──
    _ctx_box = ft.Container(width=210, bgcolor=C["surface"], border_radius=br(8), border=ft.Border.all(1, C["border"]),
                            padding=po(left=0, right=0, top=4, bottom=4), left=0, top=0,
                            shadow=ft.BoxShadow(blur_radius=12, color="#00000022", offset=ft.Offset(0, 3)))
    _ctx_layer = ft.Stack([ft.Container(expand=True, on_click=lambda e: _ctx_close()), _ctx_box], expand=True, visible=False)
    page.overlay.append(_ctx_layer)

    def _ctx_close(e=None):
        if _ctx_layer.visible:
            _ctx_layer.visible = False; page.update()

    def _ctx_row(label, fn, danger=False):
        return ft.Container(
            content=ft.Text(label, size=12, color=C["red"] if danger else C["text1"]),
            padding=po(left=14, right=14, top=7, bottom=7), ink=True,
            on_click=lambda e: (_ctx_close(), fn()))

    def _show_ctx_menu(item, gx=40.0, gy=40.0):
        if item["id"] not in _selected_ids:
            _set_sel({item["id"]}, anchor=item["id"])
        items = _sel_items()
        n = len(items)
        who = "this image" if n == 1 else f"{n} images"
        rows = [ft.Container(content=ft.Text((Path(items[0]["path"]).name if n == 1 else f"{n} selected")[:34], size=10,
                                             color=C["text3"], italic=True), padding=po(left=14, right=14, top=4, bottom=4))]
        if n == 1:
            rows.append(_ctx_row("Open", lambda _i=items[0]["id"]: _open_item(_i)))
        rows += [
            _ctx_row(f"Edit {who}…", _ctx_edit),
            _ctx_row(f"Enhance {who}", lambda: _on_batch_enhance_click()),
            _ctx_row(f"Export {who}…", lambda: asyncio.create_task(_scale_export(_sel_items()))),
            _ctx_row("Save As…" if n == 1 else "Save copies to folder…", _ctx_save),
            ft.Container(height=1, bgcolor=C["border"], margin=po(top=3, bottom=3)),
            _ctx_row(f"Delete {who}", lambda: _request_delete(_sel_items()), danger=True),
        ]
        _ctx_box.content = ft.Column(rows, spacing=0, tight=True)
        h = 30 + 32 * (len(rows) - 2) + 8
        try: pw, ph = float(page.width or 1200), float(page.height or 800)
        except Exception: pw, ph = 1200.0, 800.0
        _ctx_box.left = max(4.0, min(gx, pw - 214.0))
        _ctx_box.top = max(4.0, min(gy, ph - h - 4.0))
        _ctx_layer.visible = True
        page.update()

    def _ctx_edit():
        items = _sel_items()
        if len(items) == 1:
            current_id[0] = items[0]["id"]; load_image(items[0]); _refresh_cards()
            _fit_compact_all(); edit_overlay.visible = True; page.update()
        elif items:
            _on_batch_edit_click(None)

    def _ctx_save():
        items = _sel_items()
        if len(items) == 1:
            current_id[0] = items[0]["id"]; load_image(items[0]); _refresh_cards()
            asyncio.create_task(do_save_as())
        elif items:
            _save_selected()

    # ── one card ──
    def _make_card(item):
        iid = item["id"]
        exists = Path(item["path"]).exists()
        ts = time.strftime("%H:%M", time.localtime(item.get("ts") or 0))
        dot = (C["amber"] if (_arg_mode and item.get("is_temp"))
               else (_ACC if (item.get("parent_id") or _is_generated(item)) else "#9CA3AF"))
        badge = ft.Container(content=ft.Icon(ft.Icons.CHECK_ROUNDED, size=11, color="transparent"),
                             width=16, height=16, border_radius=br(8), alignment=ft.Alignment(0, 0),
                             border=ft.Border.all(1, C["border"]), visible=False)
        thumb = ft.Container(
            content=ft.Stack([
                (ft.Image(src=get_thumb(item["path"]), fit=ft.BoxFit.COVER, width=_CW - 6, height=_CH - 28, gapless_playback=True)
                 if exists else ft.Container(content=ft.Icon(ft.Icons.BROKEN_IMAGE_OUTLINED, color=C["text3"], size=18),
                                             alignment=ft.Alignment(0, 0), width=_CW - 6, height=_CH - 28)),
                ft.Container(content=badge, left=3, top=3),
            ]),
            width=_CW - 6, height=_CH - 28, border_radius=br(4), bgcolor=C["panel"], clip_behavior=ft.ClipBehavior.HARD_EDGE)
        card = ft.Container(
            content=ft.Column([
                thumb,
                ft.Row([ft.Text(ts, size=9, color=C["text3"]), ft.Container(expand=True),
                        ft.Container(width=6, height=6, bgcolor=dot, border_radius=br(3))],
                       spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ], spacing=3),
            width=_CW, height=_CH, padding=3, border_radius=br(6), bgcolor="#FFFFFF",
            border=ft.Border.all(1, C["border"]),
        )
        # Hover tint lives on its own transparent layer above the card: updating the card (which holds the thumbnail)
        # on every hover made the picture re-decode and flicker.
        hover_ov = ft.Container(width=_CW, height=_CH, border_radius=br(6),
                                tooltip=f"{Path(item['path']).name}\n{(item.get('prompt') or '')[:140]}".strip())
        ref = {"card": card, "badge": badge, "hover": False, "ov": hover_ov}
        _card_ctl[iid] = ref

        def _hover(e, _r=ref, _id=iid):
            _r["hover"] = str(e.data).lower() == "true"
            _r["ov"].bgcolor = "#2563EB14" if (_r["hover"] and _id not in _selected_ids) else None
            try: _r["ov"].update()
            except Exception: pass
        hover_ov.on_hover = _hover
        return ft.GestureDetector(
            content=ft.Stack([card, hover_ov], width=_CW, height=_CH), mouse_cursor=ft.MouseCursor.CLICK,
            on_tap_down=_focus_panel,
            on_tap=lambda e, _id=iid: _card_tap(_id),
            on_secondary_tap_down=lambda e, _i=item: _show_ctx_menu(_i, e.global_position.x, e.global_position.y),
        )

    async def _do_batch_edit_selected(items_to_edit: list):
        """Called from the context menu — read the API/prompt from the edit overlay"""
        api = edit_api_dd.value
        prompt_raw = (edit_prompt.value or "").strip()
        if not api:
            show_snack("⚠ Open Edit first and choose a Workflow", C["red"]); return
        if not prompt_raw:
            show_snack("⚠ Open Edit first and enter a Prompt", C["red"]); return
        await _do_batch_edit_selected_with(items_to_edit, api, prompt_raw)

    async def _do_batch_edit_selected_with(items_to_edit: list, api: str, prompt_raw: str):
        _add_ph(prompt_raw, dialog="edit"); _gen_count[0] += 1
        total = len(items_to_edit)
        for i, src_item in enumerate(items_to_edit):
            rp = resolve_wildcards(prompt_raw)
            _show_progress(rp, f"⏳ Batch {i+1}/{total}: {Path(src_item['path']).name}")
            def _p(t): _update_progress(t)
            try:
                result = await run_comfy(api, rp, img_path=src_item["path"], on_progress=_p)
                _hide_progress()
                if result:
                    ni = {"id": uuid.uuid4().hex, "path": result, "prompt": rp, "api": api,
                          "ts": time.time(), "parent_id": src_item["id"],
                          "is_temp": True, "gen": _gen_count[0]}
                    _add_ph(ni.get("prompt",""), ni.get("path"), dialog="edit")
                    history.append(ni); current_id[0] = ni["id"]
                    _mark_dirty(); load_image(ni); rebuild_history()
                else: show_snack(f"⚠ Image {i+1} failed", C["red"]); break
            except Exception as ex:
                import traceback; traceback.print_exc()
                _hide_progress(); show_snack(f"⚠ {ex}", C["red"]); break
        _selected_ids.clear()
        show_snack(f"✓ Batch Edit completed for {total} image(s)")
        rebuild_history()

    _last_cur: list = [None]
    _card_cache: dict = {}              # item id -> (what the card shows, its control)

    def rebuild_history():
        items = list(reversed(history))
        _order[:] = [i["id"] for i in items]
        _selected_ids.intersection_update(set(_order))
        if _sel_anchor[0] not in _order: _sel_anchor[0] = None
        # Cards are reused between rebuilds (a new/deleted image no longer re-creates and re-sends every thumbnail);
        # a card is rebuilt only when what it shows changed.
        ctrls = []
        for item in items:
            sig = (item["path"], item.get("ts"), bool(item.get("is_temp")), item.get("parent_id"), item.get("prompt"),
                   Path(item["path"]).exists())
            hit = _card_cache.get(item["id"])
            if hit is None or hit[0] != sig:
                hit = _card_cache[item["id"]] = (sig, _make_card(item))
            ctrls.append(hit[1])
        for gone in set(_card_cache) - set(_order):
            _card_cache.pop(gone, None); _card_ctl.pop(gone, None)
        history_row.controls[:] = ctrls
        _refresh_cards()
        _refresh_toolbar()
        page.update()
        if current_id[0] != _last_cur[0]:                      # e.g. a new image was generated: bring it into view
            _last_cur[0] = current_id[0]
            _scroll_item_into_view(current_id[0])

    _pending_batch_items: list = []  # images waiting for batch edit

    def _on_batch_enhance_click(e=None):
        sel_items=[h for h in history if h["id"] in _selected_ids]
        if not sel_items: show_snack("⚠ Select images first",C["red"]); return
        _pending_enhance_items[:]=sel_items
        _show_enhance_dialog()

    async def _do_batch_enhance(items_to_enhance,api,restore=True,upscale=False):
        _gen_count[0]+=1; total=len(items_to_enhance)
        done=failed=0; stopped=False                   # counted from real outcomes (an output was saved), never from len(selection)
        for i,src_item in enumerate(items_to_enhance):
            _show_progress("",f"⏳ Restore Face {i+1}/{total}: {Path(src_item['path']).name}")
            def _p(t): _update_progress(t)
            try:
                result=await run_enhance(api,src_item["path"],restore_face=restore,upscale=upscale,on_progress=_p)
                _hide_progress()
                if result:
                    ni={"id":uuid.uuid4().hex,"path":result,"prompt":"[Enhance] restore face",
                        "api":api,"ts":time.time(),"parent_id":src_item["id"],"is_temp":True,"gen":_gen_count[0]}
                    _add_ph(ni.get("prompt",""), ni.get("path"))
                    history.append(ni); current_id[0]=ni["id"]
                    _mark_dirty(); load_image(ni); rebuild_history()
                    done+=1
                else:
                    failed+=1; show_snack(f"⚠ Image {i+1} failed",C["red"]); break
            except GenerationStopped:
                stopped=True; _hide_progress(); break
            except Exception as ex:
                import traceback; traceback.print_exc()
                failed+=1; _hide_progress(); show_snack(f"⚠ {ex}",C["red"]); break
        _selected_ids.clear()
        left=total-done-failed                            # never processed (the batch stops at the first failure / Stop)
        if stopped:
            show_snack(f"⏹ Enhance stopped: {done}/{total} completed.",C["amber"])
        elif failed:
            show_snack(f"⚠ Enhance finished with errors: {done}/{total} completed, {failed} failed"
                       +(f", {left} not processed." if left else "."),C["red"])
        else:
            show_snack(f"✓ Restore Face completed for {done} image(s)" if done>1 else "✓ Restore Face completed")
        rebuild_history()

    async def _save_selected_async(sel_items):
        import shutil
        fp=ft.FilePicker()
        result=await fp.get_directory_path(dialog_title="Select a folder to save to")
        if not result: return
        dst_dir=Path(result); missing=0; jobs=[]; reserved=set()      # the destinations are all chosen before the first copy: reserve each one
        for item in sel_items:
            src=Path(item["path"])
            if not src.exists(): missing+=1; continue
            jobs.append((src,unique_dest(dst_dir,src.name,reserved)))     # never overwrites: same names (different folders, earlier exports) get _1, _2…
        res=await _export_util.copy_files(jobs, lambda d,n: _show_progress("",f"⏳ Saving {d}/{n}..."))    # each copy in a worker thread: the window stays responsive
        _hide_progress()
        saved=res["copied"]; failed=res["failed"]+missing
        for _err in res["errors"]: print(f"save copy failed {_err}")
        show_snack(f"✓ Saved {saved} image(s) → {dst_dir.name} ({res['seconds']:.1f}s)" if not failed
                   else f"⚠ Saved {saved} image(s) → {dst_dir.name}, {failed} failed",
                   C["green"] if not failed else C["amber"])

    def _save_selected(e=None):
        sel_items=[h for h in history if h["id"] in _selected_ids]
        if not sel_items: show_snack("⚠ Select images first",C["red"]); return
        asyncio.create_task(_save_selected_async(sel_items))

    def _on_batch_edit_click(e):
        sel_items = [h for h in history if h["id"] in _selected_ids]
        if not sel_items:
            show_snack("⚠ Select images first", C["red"]); return
        _pending_batch_items.clear()
        _pending_batch_items.extend(sel_items)
        # Open the edit overlay so the user can choose the workflow and prompt
        item = cur()
        files = scan_apis()
        edit_api_dd.options = [ft.dropdown.Option(f) for f in files]
        edit_src_lbl.value = f"Batch Edit {len(sel_items)} image(s)"
        edit_prompt.value = ""
        edit_status.value = ""
        _fit_compact_all()
        edit_overlay.visible = True
        page.update()

    # ── Images panel toolbar: "Images 128 · Select ···"  →  "Images 128 · 4 selected · Clear Edit Enhance Export Delete ···" ──
    def _bar_btn(label, on_click, primary=False, tooltip=None):
        pad = ft.Padding(left=12, right=12, top=0, bottom=0)
        if primary:
            return ft.FilledButton(label, on_click=on_click, bgcolor=_ACC, height=28, tooltip=tooltip,
                                   style=ft.ButtonStyle(padding=pad, color="#FFFFFF", text_style=ft.TextStyle(size=11)))
        return ft.OutlinedButton(label, on_click=on_click, height=28, tooltip=tooltip,
                                 style=ft.ButtonStyle(padding=pad, color=C["text2"], side=ft.BorderSide(1, C["border"]),
                                                      text_style=ft.TextStyle(size=11)))

    sel_count_lbl = ft.Text("", size=11, color=_ACC, weight=ft.FontWeight.W_600, visible=False)
    btn_select = _bar_btn("Select", lambda e: _toggle_select_mode(),
                          tooltip="Select several images without Ctrl / Shift (Ctrl+click, Shift+click and dragging a box also work)")
    btn_clear = _bar_btn("Clear", lambda e: _clear_sel(), tooltip="Clear the selection (Esc)")
    btn_edit = _bar_btn("Edit", _on_batch_edit_click, tooltip="Edit the selected images with one workflow and prompt")
    btn_enhance = _bar_btn("Enhance", lambda e: _on_batch_enhance_click(), tooltip="Restore faces on the selected images")
    btn_export = _bar_btn("Export", lambda e: asyncio.create_task(_scale_export(_sel_items())), primary=True,
                          tooltip="Scale up and export the selected images to a folder")
    btn_delete = ft.IconButton(icon=ft.Icons.DELETE_OUTLINE_ROUNDED, icon_size=17, icon_color=C["text2"], width=30, height=30,
                               padding=0, on_click=lambda e: _request_delete(_sel_items()),
                               tooltip="Delete the selected images (generated versions are deleted; source images are only removed from the list)")
    more_btn = ft.PopupMenuButton(icon=ft.Icons.MORE_HORIZ_ROUNDED, icon_color=C["text2"], icon_size=18, items=[], tooltip="More")

    def _mi(label, on_click):
        return ft.PopupMenuItem(content=ft.Text(label, size=12, color=C["text1"]), on_click=on_click)

    def _refresh_toolbar():
        n = len(_selected_ids)
        sel = n > 1 or (_select_mode[0] and n > 0)             # one image is just "the open image", not a batch
        try: narrow = float(page.width or 1200) < 900
        except Exception: narrow = False
        hist_count_lbl.value = str(len(history))
        sel_count_lbl.value = f"{n} selected"
        sel_count_lbl.visible = sel
        btn_select.content = "Done" if _select_mode[0] else "Select"
        btn_select.visible = _select_mode[0] or not sel
        btn_clear.visible = sel
        btn_edit.visible = sel and not narrow
        btn_enhance.visible = sel and not narrow
        btn_export.visible = sel
        btn_delete.visible = sel
        menu = [_mi("Select all    Ctrl+A", lambda e: _select_all())]
        if sel:
            menu.append(_mi("Clear selection    Esc", lambda e: _clear_sel()))
            if narrow:
                menu += [_mi("Edit…", lambda e: _on_batch_edit_click(e)), _mi("Enhance", lambda e: _on_batch_enhance_click())]
            menu.append(_mi("Save copies to folder…", lambda e: _save_selected()))
        menu += [ft.PopupMenuItem(),
                 _mi("Save current image As…", lambda e: asyncio.create_task(do_save_as())),
                 ft.PopupMenuItem(),
                 _mi("Remove all from this list…", lambda e: _request_delete(list(history), clear_all=True))]
        more_btn.items = menu

    def _on_panel_size(e):
        _strip["view"] = float(e.width or 0)
        _refresh_toolbar()
        try: page.update()
        except Exception: pass

    def _on_resize_toolbar():
        _refresh_toolbar()
        try: page.update()
        except Exception: pass

    mq_rect = ft.Container(left=0, top=0, width=1, height=1, visible=False, bgcolor="#2563EB26",
                           border=ft.Border.all(1, _ACC), border_radius=br(2))
    strip_gd = ft.GestureDetector(
        content=ft.Stack([ft.Container(content=history_row, padding=po(left=_PADX, right=_PADX, top=_PADY, bottom=_PADY),
                                       expand=True), mq_rect], expand=True),
        expand=True, drag_interval=30,
        on_pan_down=_mq_down, on_pan_start=_mq_start, on_pan_update=_mq_update, on_pan_end=_mq_end, on_pan_cancel=_mq_end,
        on_tap=_clear_sel, on_scroll=_on_strip_wheel, on_enter=_focus_panel,
    )
    panel_kb = ft.KeyboardListener(
        content=ft.Container(
            content=ft.Column([
                ft.Container(
                    content=ft.Row([
                        ft.Row([ft.Text("Images", size=12, weight=ft.FontWeight.W_600, color=C["text1"]), hist_count_lbl,
                                sel_count_lbl], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        ft.Row([btn_select, btn_clear, btn_edit, btn_enhance, btn_export, btn_delete, more_btn], spacing=6,
                               vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    padding=po(left=14, right=10, top=5, bottom=5),
                    border=ft.Border(bottom=ft.BorderSide(1, C["border"])),
                ),
                ft.Container(content=strip_gd, expand=True, clip_behavior=ft.ClipBehavior.HARD_EDGE, on_size_change=_on_panel_size),
            ], spacing=0),
            height=170, bgcolor=C["surface"], border=ft.Border(top=ft.BorderSide(1, C["border"])),   # strip = card 100 + padding + the horizontal scrollbar
        ),
        autofocus=True, on_key_down=_on_kb_down, on_key_up=_on_kb_up,
    )
    history_panel = panel_kb

    # ── Layout ─────────────────────────────────────────────────────────
    top_panel=ft.Container(
        content=ft.Column([
            toolbar,
            ft.Container(
                content=ft.Stack([
                    ft.GestureDetector(
                        content=viewer_scroll,
                        on_double_tap=open_fullscreen,
                        on_scroll=_on_wheel,
                        on_pan_update=_on_pan,
                        expand=True,
                    ),
                    mask_gesture,
                ]),
                expand=True, bgcolor=C["bg"], on_size_change=_on_vp_size,
            ),
            mask_status_bar,
            info_bar,
        ],spacing=0,expand=True),
        expand=True,
    )

    # Row 1: app identity + the active Working Folder (context, not a manager).
    header=ft.Container(
        content=ft.Row([
            ft.Row([ft.Icon(ft.Icons.PHOTO_CAMERA_OUTLINED,color=C["accent"],size=16),
                    ft.Text("Image Editor",size=13,weight=ft.FontWeight.W_700,color=C["text1"])],spacing=6),
            *([] if _arg_mode else [ft.Container(width=1,height=18,bgcolor=C["border"]), folder_menu]),
        ],spacing=14,vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor=C["toolbar"],padding=po(left=16,right=16,top=5,bottom=5),
        border=ft.Border(bottom=ft.BorderSide(1,C["border"])),height=42,
    )

    # ── Startup: reopen the last Working Folder if it still exists, otherwise start with none ──
    def _startup_load():
        last = load_editor_config().get("last_working_folder")
        if isinstance(last, str) and last and Path(last).is_dir():
            if _open_working_folder(last, startup=True): return
        _refresh_folder_ui(); load_image(None); rebuild_history()

    if _arg_mode:
        for _ap in _arg_paths:
            _item = {"id": uuid.uuid4().hex, "path": _ap, "prompt": "(imported)", "api": "",
                     "ts": time.time(), "parent_id": None, "is_temp": False, "is_arg": True}
            history.append(_item)
        current_id[0] = history[-1]["id"]
        load_image(cur()); rebuild_history()
    else:
        _startup_load()
    _refresh_tools()

    def _on_window_event(e):
        if e.data in ("blur", "focus"): _mods["ctrl"] = False; _mods["shift"] = False   # a key may have been released elsewhere
        if e.data == "close" and _arg_mode:
            try: shutil.rmtree(_arg_session_temp, ignore_errors=True)
            except: pass
    page.window.on_event = _on_window_event

    page.add(ft.Column([header,
        ft.Column([top_panel,history_panel],spacing=0,expand=True),
    ],spacing=0,expand=True))
    _update_check.start_notice(ft, page, APP_NAME, APP_VERSION)      # a notice only (GitHub releases, once a day, silent when offline); see update_check.py

    # ── Drag & Drop images from Explorer ──────────────────────────────────
    def _on_file_drop(e: ft.FileDropEvent):
        if not _ws_needed(): return                       # no Working Folder yet: nothing to import into
        added = _import_paths([f_path for f_path in e.files])
        if added:
            show_snack(f"✓ Imported {added} image(s)", C["green"])

    page.on_file_drop = _on_file_drop
    page.on_resize = lambda e: (_apply_density(e), _fit_new_dialog(), _fit_compact_all(), _on_resize_toolbar(), _on_extend_resize())
    _apply_density()



ft.app(target=_app_error.guard(ft, main, APP_NAME, APP_VERSION))   # friendly error dialog instead of Flet's red 'application crashed' page