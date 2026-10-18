"""Build the Windows portable release: PyInstaller one-folder EXEs + resources + notices -> folder + ZIP.

Usage:
    python make_release.py                # build EXEs, validate, stage dist/WorkflowPad-v0.1.3-Beta, zip
    python make_release.py --skip-build   # re-stage from the last dist/_build/dist output (no PyInstaller run)
    python make_release.py --no-zip       # stage the folder only

Pipeline:  environment check -> PyInstaller (WorkflowPad.spec) -> stage -> validate -> zip.
It only writes dist/ (dist/_build holds PyInstaller's work files); project source files are never modified or deleted.

What ships: StoryGenerator.exe (Story Generator), ImageEditor.exe, PromptLab.exe, BatchEditor.exe, GalleryGenerator.exe, _internal/ (runtime), comfy_workflow/ (example ComfyUI workflows + README), comfy_apis/ (empty: image, llm, caption, enhance), icons/,
README.txt, THIRD_PARTY_NOTICES.txt and licenses/. YOLO .pt models are NEVER shipped — users install them from the app.
The resource copy is allowlist-based on purpose: a new file in the working tree is left out until it is added here.
"""
import argparse
import importlib.metadata as md
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RELEASE_NAME = "WorkflowPad-v0.1.3-Beta"
BUILD_DIR = ROOT / "dist" / "_build"
BUILT = BUILD_DIR / "dist" / "WorkflowPad"          # PyInstaller COLLECT output (name set in WorkflowPad.spec)
EXES = ("StoryGenerator.exe", "ImageEditor.exe", "PromptLab.exe", "BatchEditor.exe", "GalleryGenerator.exe")

# ── Read-only resources placed next to the EXEs (apps resolve them relative to the EXE: constants.APP_ROOT) ──
# (folder, glob) — only matching files are copied
TREES = [
    ("comfy_workflow", "*.json"),   # example ComfyUI workflows the user opens in ComfyUI, edits, then exports with Export (API)
    ("comfy_workflow", "*.txt"),    # ... and the README that teaches it
    ("icons",   "*.ico"),
    ("helper",  "*.txt"),       # text shown by the Help button in Story Generator
]
# The user's exported API files go here (the apps read them). Shipped EMPTY on purpose: the release never carries the author's own workflows.
COMFY_APIS = {
    "image_create": "Image-creation workflows (text-to-image only: they must not need a source image). Put the file you made with ComfyUI > Export (API) here.",
    "image_edit":   "Image-edit workflows (they need a source image: edit, img2img, inpaint, mask edit, identity pass). Put the file you made with ComfyUI > Export (API) here.",
    "llm":     "LLM chat workflows. Put the exported .json file here (any name, for example gemma4_llm.json). It needs the LLMSessionChatNode.",
    "caption": "Image-to-prompt workflows (for example JoyCaption or Florence2). Put the exported file here.",
    "enhance": "Enhance workflows (face restore, upscale, outpaint). Put the exported file here.",
}
EMPTY_DIRS = []                   # user data (stories, last sessions) lives in %APPDATA%\WorkflowPad, never in the program folder

# Never copied, whatever else says otherwise.
EXCLUDE_NAMES = {"llm_chat_config.json", "session.log"}
EXCLUDE_PATTERNS = [re.compile(r"^saved_.*\.json$"), re.compile(r".*_backup_.*\.py$")]

# ── What must never appear inside a shipped text file ─────────────────
LEAK_PATTERNS = [
    ("windows drive path", re.compile(r"[A-Za-z]:[\\/]{1,2}(?:AI|Users|ComfyUI)", re.I)),
    ("user home",          re.compile(r"[\\/]Users[\\/][A-Za-z0-9._-]+", re.I)),
    ("absolute drive path", re.compile(r"[\"'][A-Za-z]:[\\/]")),
    ("bearer token",       re.compile(r"Bearer\s+[A-Za-z0-9._\-]{16,}")),
    ("openai-style key",   re.compile(r"\bsk-[A-Za-z0-9]{16,}")),
    ("xai key",            re.compile(r"\bxai-[A-Za-z0-9]{16,}")),
    ("assigned api key",   re.compile(r"api[_-]?key\s*[:=]\s*[\"'][A-Za-z0-9._\-]{16,}[\"']", re.I)),
]
SCAN_SUFFIXES = {".json", ".md", ".txt", ".cfg", ".ini"}

# Files that must not be in the release at all (checked over the whole staged tree).
FORBIDDEN_NAME = re.compile(r"(\.pt$|\.pth$|^saved_.*\.json$|^credentials.*\.json$|^session\.log$|"
                            r"^llm_chat_config\.json$|^image_editor_config\.json$)", re.I)
CUDA_LIB = re.compile(r"(cudart|cublas|cudnn|cufft|curand|cusolver|cusparse|nvrtc|nvjitlink|nvtx|nccl)", re.I)

README = ROOT / "build_tools" / "README_release.txt"
LICENSE_FILE = ROOT / "LICENSE"
MANUAL = ROOT / "docs" / "WorkflowPad-Manual.html"      # the illustrated user manual (one self-contained file)
SOURCE_URL = "https://github.com/sudja-tongtip/WorkflowPad"


_extra_excludes: set = set()      # --exclude NAME (e.g. a workflow that still contains a machine-specific path)


def _excluded(name: str) -> bool:
    return name in EXCLUDE_NAMES or name in _extra_excludes or any(p.match(name) for p in EXCLUDE_PATTERNS)


# ── 1. environment ────────────────────────────────────────────────────
def check_environment() -> list[str]:
    problems = []
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        problems.append("PyInstaller is not installed (pip install pyinstaller)")
    probe = ("import json,torch;print(json.dumps({'v':torch.__version__,'cuda':torch.version.cuda}))")
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    if r.returncode != 0:
        problems.append("PyTorch is not importable in the build environment (needed by Ultralytics)")
    else:
        info = json.loads(r.stdout.strip().splitlines()[-1])
        print(f"  torch {info['v']} (cuda build: {info['cuda']})")
        if info["cuda"]:
            problems.append(f"PyTorch {info['v']} is a CUDA build — the release must be CPU-only. Install the CPU wheel: "
                            "pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu")
    for mod in ("ultralytics", "flet", "flet_desktop", "PIL", "pythainlp"):
        if subprocess.run([sys.executable, "-c", f"import {mod}"], capture_output=True).returncode != 0:
            problems.append(f"module '{mod}' is not importable in the build environment (see requirements.txt)")
    return problems


# ── 2. PyInstaller ────────────────────────────────────────────────────
def run_pyinstaller() -> bool:
    env = dict(os.environ)
    for k in ("WP_CONSOLE", "WP_SMOKE"):        # a release is windowed and carries no diagnostic EXE
        env.pop(k, None)
    cmd = [sys.executable, "-m", "PyInstaller", "WorkflowPad.spec", "--noconfirm", "--clean",
           "--distpath", str(BUILD_DIR / "dist"), "--workpath", str(BUILD_DIR / "work")]
    print("  " + " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT, env=env).returncode == 0


# ── 3. stage ──────────────────────────────────────────────────────────
def stage(dest: Path) -> list[Path]:
    shutil.copytree(BUILT, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc.bak", "WorkflowPadSmoke*"))
    copied = []
    for folder, pattern in TREES:
        src_dir = ROOT / folder
        if not src_dir.is_dir():
            print(f"  skip (absent): {folder}/")
            continue
        out_dir = dest / folder
        out_dir.mkdir(parents=True, exist_ok=True)
        for src in sorted(src_dir.glob(pattern)):
            if _excluded(src.name):
                continue
            shutil.copy2(src, out_dir / src.name)
            copied.append(out_dir / src.name)
    for d in EMPTY_DIRS:
        (dest / d).mkdir(exist_ok=True)
    for sub, hint in COMFY_APIS.items():
        d = dest / "comfy_apis" / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / "README.txt").write_text(hint + "\nSee comfy_workflow/README.txt for the steps.\n", encoding="utf-8")
        copied.append(d / "README.txt")
    if README.is_file():
        shutil.copy2(README, dest / "README.txt")
        copied.append(dest / "README.txt")
    if MANUAL.is_file():
        shutil.copy2(MANUAL, dest / MANUAL.name)
        copied.append(dest / MANUAL.name)
    if LICENSE_FILE.is_file():
        shutil.copy2(LICENSE_FILE, dest / "LICENSE.txt")
        copied.append(dest / "LICENSE.txt")
    copied += write_notices(dest)
    return copied


def _bundled_distributions(internal: Path):
    """Installed distributions whose top-level module/package is present in the bundle."""
    names = {p.name.split(".")[0] for p in internal.iterdir()}
    seen = set()
    for dist in md.distributions():
        name = dist.metadata["Name"]
        if not name or name.lower() in seen:
            continue
        tops = (dist.read_text("top_level.txt") or "").split()
        if not tops and dist.files:
            tops = sorted({Path(str(f)).parts[0].split(".")[0] for f in dist.files if len(Path(str(f)).parts) > 1})
        if any(t in names for t in tops if not t.startswith("_") or t in names):
            seen.add(name.lower())
            yield dist


def write_notices(dest: Path) -> list[Path]:
    """THIRD_PARTY_NOTICES.txt (name, version, license) + licenses/<name>-<ver>/ copies of each package's license files."""
    internal = dest / "_internal"
    rows, written = [], []
    lic_root = dest / "licenses"
    for dist in sorted(_bundled_distributions(internal), key=lambda d: d.metadata["Name"].lower()):
        meta = dist.metadata
        name, ver = meta["Name"], meta["Version"]
        lic = (meta.get("License-Expression") or meta.get("License") or "").strip().splitlines()
        lic = (lic[0][:90] if lic else "") or "; ".join(c.split("::")[-1].strip() for c in meta.get_all("Classifier") or []
                                                         if c.startswith("License ::")) or "see package"
        rows.append((name, ver, lic))
        for f in dist.files or []:
            if re.search(r"(^|[\\/])(licen[cs]e|copying|notice)[^\\/]*$", str(f), re.I):
                try:
                    out = lic_root / f"{name}-{ver}" / Path(str(f)).name
                    out.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dist.locate_file(f), out)
                    written.append(out)
                except OSError:
                    pass
    lines = [
        "WorkflowPad — third-party software included in this folder",
        "=" * 60, "",
        "WorkflowPad is Copyright (c) 2026 Sudja, licensed under the GNU Affero General Public License v3.0 (AGPL-3.0, LICENSE.txt).",
        "Complete corresponding source for this release:",
        f"  {SOURCE_URL}",
        "",
        "NOTE: this package bundles Ultralytics, also licensed AGPL-3.0 (see licenses/ultralytics-*/). Other bundled",
        "packages keep their own licenses, listed below.",
        "Pretrained YOLO weights are NOT included; they are downloaded by the user from github.com/ultralytics/assets",
        "and are covered by Ultralytics' own terms.", "",
        f"{'Package':32} {'Version':14} License", "-" * 78,
    ] + [f"{n[:31]:32} {v[:13]:14} {l}" for n, v, l in rows] + ["", "License texts: licenses/<package>-<version>/"]
    notice = dest / "THIRD_PARTY_NOTICES.txt"
    notice.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  notices: {len(rows)} packages, {len(written)} license files")
    return [notice] + written


# ── 4. validate ───────────────────────────────────────────────────────
def scan(files: list[Path], dest: Path) -> list[str]:
    problems = []
    for f in files:
        if f.suffix.lower() not in SCAN_SUFFIXES or "licenses" in f.relative_to(dest).parts:
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            problems.append(f"{f.relative_to(dest)}: unreadable ({exc})")
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for label, pat in LEAK_PATTERNS:
                if label == "absolute drive path" and f.suffix.lower() == ".md":
                    continue
                m = pat.search(line)
                if m:
                    problems.append(f"{f.relative_to(dest)}:{lineno}: {label}: {m.group(0)[:60]}")
    return problems


def validate_workflows(dest: Path) -> list[str]:
    problems = []
    for folder in ("comfy_workflow",):
        d = dest / folder
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            try:
                json.loads(f.read_text(encoding="utf-8"))
            except Exception as exc:
                problems.append(f"{f.relative_to(dest)}: invalid JSON ({exc})")
    return problems


def validate_layout(dest: Path) -> list[str]:
    problems = []
    for exe in EXES:
        if not (dest / exe).is_file():
            problems.append(f"missing {exe}")
    if not (dest / MANUAL.name).is_file():
        problems.append(f"{MANUAL.name} missing (build docs/ first)")
    if not (dest / "LICENSE.txt").is_file():
        problems.append("LICENSE.txt missing (add a LICENSE file at the project root)")
    internal = dest / "_internal"
    if not list(internal.glob("python3*.dll")):
        problems.append("_internal/ has no Python runtime DLL")
    if not (internal / "flet_client" / "flet.exe").is_file():
        problems.append("_internal/flet_client/flet.exe missing (the UI would try to download it on first run)")
    for need in ("comfy_workflow", "icons"):
        if not any((dest / need).glob("*")):
            problems.append(f"{need}/ is empty or missing")
    if not (dest / "comfy_workflow" / "README.txt").is_file():
        problems.append("comfy_workflow/README.txt missing")
    for sub in COMFY_APIS:
        if not (dest / "comfy_apis" / sub).is_dir():
            problems.append(f"comfy_apis/{sub}/ missing")
        elif any((dest / "comfy_apis" / sub).glob("*.json")):
            problems.append(f"comfy_apis/{sub}/ must ship empty (the user's own exported workflows go there)")
    for ico in ("story_generator", "image_editor", "prompt_lab", "batch_editor", "gallery_generator"):
        if not (dest / "icons" / f"{ico}.ico").is_file():
            problems.append(f"icons/{ico}.ico missing")
    for p in dest.rglob("*"):
        if p.is_file():
            if FORBIDDEN_NAME.search(p.name):
                problems.append(f"forbidden file in release: {p.relative_to(dest)}")
            if p.suffix.lower() in (".dll", ".pyd") and CUDA_LIB.search(p.name):
                problems.append(f"CUDA library bundled: {p.relative_to(dest)}")
        elif p.is_dir() and p.name == "__pycache__":
            problems.append(f"__pycache__ in release: {p.relative_to(dest)}")
    return problems


def folder_size_mb(p: Path) -> float:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) / (1024 * 1024)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-zip", action="store_true", help="stage the folder only")
    ap.add_argument("--skip-build", action="store_true", help="reuse dist/_build/dist from the last PyInstaller run")
    ap.add_argument("--out", default="dist", help="output directory (default: dist)")
    ap.add_argument("--build-dir", default="dist/_build", help="PyInstaller work/output folder (default: dist/_build); "
                    "use another one while an EXE built there is still running")
    ap.add_argument("--exclude", action="append", default=[], metavar="FILE",
                    help="leave a resource file out of the release (repeatable), e.g. --exclude gemma4_llm.json")
    args = ap.parse_args()
    _extra_excludes.update(args.exclude)
    global BUILD_DIR, BUILT
    BUILD_DIR = ROOT / args.build_dir
    BUILT = BUILD_DIR / "dist" / "WorkflowPad"

    if not args.skip_build:
        print("1/4 Checking build environment")
        problems = check_environment()
        if problems:
            print("\nBUILD ENVIRONMENT NOT READY:\n" + "\n".join("  - " + p for p in problems))
            return 1
        print("2/4 PyInstaller one-folder build (several minutes)")
        if not run_pyinstaller():
            print("\nPyInstaller failed — see its output above. Nothing was staged.")
            return 1
    if not all((BUILT / e).is_file() for e in EXES):
        print(f"\nNo build output at {BUILT} (run without --skip-build)")
        return 1

    out_root = ROOT / args.out
    dest = out_root / RELEASE_NAME
    if dest.exists():
        shutil.rmtree(dest)
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"3/4 Staging {dest}")
    copied = stage(dest)

    print("4/4 Validating")
    problems = validate_layout(dest) + validate_workflows(dest) + scan(copied, dest)
    if problems:
        print("\nRELEASE BLOCKED:\n")
        for p in problems:
            print("  " + p)
        print(f"\nFix these, then rebuild. Leaving {dest} in place for inspection.")
        return 1
    print(f"  layout ok, no .pt models, no CUDA libs, scan clean — {folder_size_mb(dest):.0f} MB uncompressed")

    if not args.no_zip:
        archive = shutil.make_archive(str(out_root / RELEASE_NAME), "zip", root_dir=out_root, base_dir=RELEASE_NAME)
        print(f"\nReady: {archive} ({Path(archive).stat().st_size / (1024 * 1024):.0f} MB)")
    else:
        print(f"\nReady: {dest}")
    print("\nBefore publishing: see the licensing notes in THIRD_PARTY_NOTICES.txt (Ultralytics AGPL-3.0) — "
          "an EXE that bundles it is not cleared for distribution automatically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
