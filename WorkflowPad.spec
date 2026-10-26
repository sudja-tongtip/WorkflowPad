# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder build: StoryGenerator.exe (Story Generator), ImageEditor.exe, PromptLab.exe, BatchEditor.exe,
GalleryGenerator.exe, MusicStudio.exe and SimpleChat.exe in ONE shared folder.

    python make_release.py            # runs this spec, validates, stages and zips (the normal way)
    python -m PyInstaller WorkflowPad.spec --noconfirm --distpath dist/_build/dist --workpath dist/_build/work

Both EXEs are collected into a single COLLECT, so the Python runtime / Flet / Pillow files they share are stored once
in _internal/. Only StoryGenerator.exe gets PyTorch + Ultralytics (Image Editor does not use them).
Read-only resources (APIs/, llm/, enhance/, icons/, helper/) are NOT bundled here: make_release.py places them next to
the EXEs, because the apps resolve them relative to the EXE (constants.APP_ROOT) and users add their own workflows.
YOLO .pt models are never bundled (downloaded by the user via the in-app Install Models dialog).
Set WP_CONSOLE=1 to build with a console window for debugging.
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve()
CONSOLE = os.environ.get("WP_CONSOLE") == "1"

# ── Flet desktop client (flet.exe + Flutter DLLs). Normally downloaded to ~/.flet on first run; bundled instead. ──
import flet_desktop.version as _fv
_client = Path.home() / ".flet" / "client" / f"flet-desktop-full-{_fv.version}" / "flet"
if not (_client / "flet.exe").is_file():
    raise SystemExit(f"Flet client {_fv.version} not found at {_client}. Run any Flet app once on this machine "
                     "(it downloads the client), then build again.")
flet_client = Tree(str(_client), prefix="flet_client")   # plain data: never dependency-scanned, no duplicate DLLs

# ── Story Generator ───────────────────────────────────────────────────────────────────────────────────────────
sg_hidden = (
    [m for m in collect_submodules("story_generator") if ".tests" not in m]   # _print_versions imports by name
    + collect_submodules("flet")
    + collect_submodules("flet_desktop")
    + ["PIL.ImageFont", "PIL.ImageDraw", "PIL.ImageFilter", "PIL.ImageOps"]
)
sg_datas = (
    collect_data_files("ultralytics")            # cfg/*.yaml etc. that the YOLO loader reads at import time
    + collect_data_files("pythainlp")            # tokenizer dictionaries
    + collect_data_files("flet")
)
# Things nothing here imports but that big packages can drag in.
sg_excludes = ["tkinter", "IPython", "jupyter", "notebook", "pytest", "tensorboard", "triton", "onnx", "onnxruntime",
               "tensorflow", "keras", "torchaudio", "polars", "_polars_runtime_32", "polars_runtime_32", "test", "unittest.test", "story_generator.tests"]

a_sg = Analysis(
    ["story_generator.py"], pathex=[str(ROOT)], binaries=[], datas=sg_datas,
    hiddenimports=sg_hidden, hookspath=[], runtime_hooks=[str(ROOT / "build_tools" / "flet_view_hook.py")],
    excludes=sg_excludes, noarchive=False,
)
pyz_sg = PYZ(a_sg.pure)
exe_sg = EXE(
    pyz_sg, a_sg.scripts, [], exclude_binaries=True, name="StoryGenerator", console=CONSOLE,
    icon=str(ROOT / "icons" / "story_generator.ico"), contents_directory="_internal",
)

# ── Image Editor (no torch / ultralytics / numpy needed) ─────────────────────────────────────────────────────
ie_hidden = collect_submodules("flet") + collect_submodules("flet_desktop") + ["flet_code_editor"]
ie_excludes = sg_excludes + ["torch", "torchvision", "ultralytics", "cv2", "numpy", "scipy", "pandas", "matplotlib",
                             "polars", "pythainlp", "sympy", "networkx"]
a_ie = Analysis(
    ["image_editor.py"], pathex=[str(ROOT)], binaries=[], datas=collect_data_files("flet"),
    hiddenimports=ie_hidden, hookspath=[], runtime_hooks=[str(ROOT / "build_tools" / "flet_view_hook.py")],
    excludes=ie_excludes, noarchive=False,
)
pyz_ie = PYZ(a_ie.pure)
exe_ie = EXE(
    pyz_ie, a_ie.scripts, [], exclude_binaries=True, name="ImageEditor", console=CONSOLE,
    icon=str(ROOT / "icons" / "image_editor.ico"), contents_directory="_internal",
)

parts = [exe_sg, a_sg.binaries, a_sg.datas, exe_ie, a_ie.binaries, a_ie.datas, flet_client]

# Prompt Lab, Batch Editor, Gallery Generator: Flet + Pillow apps, same lean excludes as Image Editor.
# Music Studio (the music_studio package + the lyrics editor control) and Simple Chat Studio are the same kind of Flet app.
for _script, _name, _icon, _extra in (("prompt_lab.py", "PromptLab", "prompt_lab.ico", []),
                                      ("batch_editor.py", "BatchEditor", "batch_editor.ico", []),
                                      ("gallery_generator.py", "GalleryGenerator", "gallery_generator.ico", []),
                                      ("music_studio.py", "MusicStudio", "music_studio.ico", collect_submodules("music_studio") + ["flet_code_editor"]),
                                      ("simple_chat.py", "SimpleChat", "simple_chat.ico", [])):
    _a = Analysis([_script], pathex=[str(ROOT)], binaries=[], datas=collect_data_files("flet"),
                  hiddenimports=collect_submodules("flet") + collect_submodules("flet_desktop") + _extra, hookspath=[],
                  runtime_hooks=[str(ROOT / "build_tools" / "flet_view_hook.py")], excludes=ie_excludes, noarchive=False)
    _exe = EXE(PYZ(_a.pure), _a.scripts, [], exclude_binaries=True, name=_name, console=CONSOLE,
               icon=str(ROOT / "icons" / _icon), contents_directory="_internal")
    parts += [_exe, _a.binaries, _a.datas]

if os.environ.get("WP_SMOKE") == "1":      # diagnostic only — make_release.py never stages this EXE
    a_sm = Analysis(["build_tools/smoke_frozen.py"], pathex=[str(ROOT)], datas=sg_datas, hiddenimports=sg_hidden,
                    runtime_hooks=[str(ROOT / "build_tools" / "flet_view_hook.py")], excludes=sg_excludes)
    exe_sm = EXE(PYZ(a_sm.pure), a_sm.scripts, [], exclude_binaries=True, name="WorkflowPadSmoke", console=True,
                 contents_directory="_internal")
    parts += [exe_sm, a_sm.binaries, a_sm.datas]

coll = COLLECT(*parts, strip=False, upx=False, name="WorkflowPad")
