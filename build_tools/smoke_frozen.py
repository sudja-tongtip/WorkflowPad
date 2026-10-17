"""Frozen-environment smoke check. Built only with WP_SMOKE=1 (never shipped); prints one JSON line per check.

It exercises what a plain launch would not: path resolution, the bundled Flet client, Pillow, Thai tokenizer and the
YOLO library (import + status). Nothing is downloaded and ComfyUI is not contacted.
"""
import json
import os
import sys
import traceback
from pathlib import Path


def check(name, fn):
    try:
        print(json.dumps({"check": name, "ok": True, "value": fn()}, ensure_ascii=True, default=str), flush=True)
    except Exception as exc:
        print(json.dumps({"check": name, "ok": False, "error": f"{type(exc).__name__}: {exc}",
                          "tb": traceback.format_exc()[-600:]}, ensure_ascii=True), flush=True)


def _paths():
    from story_generator import constants as c
    return {"frozen": getattr(sys, "frozen", False), "exe": sys.executable, "APP_ROOT": str(c.APP_ROOT),
            "API_DIR_exists": c.API_DIR.is_dir(), "API_DIR": str(c.API_DIR.relative_to(c.APP_ROOT)), "icons_exist": (c.APP_ROOT / "icons").is_dir(),
            "FLET_VIEW_PATH": os.environ.get("FLET_VIEW_PATH"),
            "flet_exe": os.path.isfile(os.path.join(os.environ.get("FLET_VIEW_PATH", ""), "flet.exe")),
            "meipass_is_not_root": str(getattr(sys, "_MEIPASS", "")) != str(c.APP_ROOT)}


def _imports():
    import flet, PIL, numpy
    from story_generator import main_ui, comfy_api, llm_engine_groom  # noqa: F401  (whole app imports)
    return {"flet": flet.__version__, "PIL": PIL.__version__, "numpy": numpy.__version__}


def _thai():
    from pythainlp.tokenize import word_tokenize
    return word_tokenize("ฉันกินข้าวที่บ้าน")


def _yolo():
    from story_generator import comfy_api as ca
    s = ca.yolo_runtime_status()
    return {"library": s["library"], "person": s["person"], "face": s["face"], "models_dir": str(ca.user_models_dir()),
            "inside_bundle": str(ca.user_models_dir()).startswith(str(getattr(sys, "_MEIPASS", "<none>")))}


def _torch():
    import torch
    return {"version": torch.__version__, "cuda_build": torch.version.cuda, "cuda_available": torch.cuda.is_available()}


def _pil_fallback():
    # no models + no detector: the narration path must return "no detector" instead of raising
    from story_generator import comfy_api as ca
    os.environ["WORKFLOWPAD_YOLO_WEIGHTS"] = os.path.join(os.environ.get("TEMP", "."), "does_not_exist.pt")
    return ca._detect_subject_boxes("nonexistent.jpg") is None or "detector returned a value"


def _launch_path():
    # what Story Generator's "open in Image Editor" would start — captured, not launched
    import subprocess
    from story_generator import main_ui
    seen = []
    real = subprocess.Popen
    subprocess.Popen = lambda args, **kw: seen.append((list(args), kw.get("cwd")))
    try:
        main_ui._launch_image_editor(r"C:\some\image.png")
    finally:
        subprocess.Popen = real
    exe = seen[0][0][0]
    return {"exe": exe, "exists": os.path.isfile(exe), "same_dir_as_launcher": os.path.dirname(exe) == os.path.dirname(sys.executable),
            "image_arg": seen[0][0][1:]}


for _n, _f in (("paths", _paths), ("launch_image_editor", _launch_path), ("imports", _imports), ("thai", _thai), ("torch", _torch), ("yolo_status", _yolo),
               ("fallback_without_models", _pil_fallback)):
    check(_n, _f)
