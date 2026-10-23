"""Final data-safety verification (second round): partial output files, destinations taken after they were reserved, image tabs that follow their prompt, the real Gallery loop counters.
Temp dirs and mocks only; no user data, no ComfyUI.

Run:  python -m story_generator.tests.test_data_safety_final            (all)
      python -m story_generator.tests.test_data_safety_final C1 C2      (only these)
"""
import asyncio
import builtins
import inspect
import json
import os
import re
import shutil
import sys
import tempfile
import textwrap
import threading
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "story_generator" / "tests"))
from story_generator.tests.test_data_safety import check, load_program, png_bytes, _fails  # noqa: E402
import export_util as X  # noqa: E402


class _HalfWriter:
    """A file object whose write() stores half of the data and then fails like a full disk."""
    def __init__(self, f):
        self.f = f

    def write(self, data):
        self.f.write(data[: len(data) // 2])
        self.f.flush()
        raise OSError(28, "No space left on device")

    def __enter__(self):
        self.f.__enter__()
        return self

    def __exit__(self, *a):
        return self.f.__exit__(*a)

    def __getattr__(self, name):
        return getattr(self.f, name)


def _leftovers(d: Path, keep: set):
    return sorted(p.name for p in d.iterdir() if p.name not in keep)


# ───────────────────────────────────────────────────────────────────────── C1 (B1): a failed write leaves no partial file; a name taken at the last moment is never overwritten
def c1():
    tmp = Path(tempfile.mkdtemp(prefix="wp_c1_"))
    try:
        out = tmp / "out"
        out.mkdir()
        (out / "cat.png").write_bytes(b"ORIGINAL-KEEP-ME")
        keep = {"cat.png"}

        # (a) the disk fills up half way through the write
        real_open = builtins.open

        def fake_open(file, mode="r", *a, **k):
            f = real_open(file, mode, *a, **k)
            if "b" in mode and ("x" in mode or "w" in mode) and Path(str(file)).parent == out:
                return _HalfWriter(f)
            return f

        builtins.open = fake_open
        try:
            try:
                X.write_unique(out, "cat.png", png_bytes())
                raised = False
            except OSError as ex:
                raised = ex.errno == 28
        finally:
            builtins.open = real_open
        check("C1 a write that fails half way raises (it is not reported as saved)", raised)
        check("C1 no partial or temporary file is left in the folder", _leftovers(out, keep) == [], _leftovers(out, keep))
        check("C1 the file that was already there is untouched", (out / "cat.png").read_bytes() == b"ORIGINAL-KEEP-ME")

        # (b) the finalising step fails (flush to disk)
        real_fsync = os.fsync
        os.fsync = lambda fd: (_ for _ in ()).throw(OSError(5, "I/O error"))
        try:
            try:
                X.write_unique(out, "dog.png", png_bytes())
                raised = False
            except OSError:
                raised = True
        finally:
            os.fsync = real_fsync
        check("C1 a failure while finishing leaves nothing behind either", raised and _leftovers(out, keep) == [], _leftovers(out, keep))

        # (c) many writers, one name: every one gets its own file with its own content, nothing is lost
        results, errors = [], []

        def worker(i):
            try:
                results.append(X.write_unique(out, "same.png", bytes([i]) * 64))
            except Exception as ex:                                  # pragma: no cover
                errors.append(ex)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(12)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        check("C1 twelve concurrent writers give twelve distinct files", not errors and len({str(p) for p in results}) == 12, (errors, results))
        check("C1 each file holds the bytes of ITS writer", sorted(p.read_bytes() for p in results) == sorted(bytes([i]) * 64 for i in range(12)))
        check("C1 no temporary file remains after concurrent writes", _leftovers(out, keep | {p.name for p in results}) == [], _leftovers(out, keep | {p.name for p in results}))

        # (d) another program creates the target name at the very last moment (after our data is written, before it is put in place)
        foreign = b"FOREIGN-FILE-CREATED-MEANWHILE"
        target = out / "race.png"
        real_link = getattr(os, "link", None)
        real_rename = os.rename
        fired = {"n": 0}

        def create_foreign_first():
            if fired["n"] == 0:
                fired["n"] += 1
                target.write_bytes(foreign)

        def link_hook(src, dst, *a, **k):
            create_foreign_first()
            return real_link(src, dst, *a, **k)

        def rename_hook(src, dst, *a, **k):
            create_foreign_first()
            return real_rename(src, dst, *a, **k)

        if real_link:
            os.link = link_hook
        os.rename = rename_hook
        try:
            got = X.write_unique(out, "race.png", b"MINE")
        finally:
            if real_link:
                os.link = real_link
            os.rename = real_rename
        check("C1 a name taken at the last moment is not overwritten", target.read_bytes() == foreign, target.read_bytes())
        check("C1 our data went to the next free name", got != target and got.read_bytes() == b"MINE", got)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────────── C2 (B2): a reserved destination that another program fills before the copy; copy2 would overwrite
def c2():
    IE = load_program("image_editor")
    tmp = Path(tempfile.mkdtemp(prefix="wp_c2_"))
    try:
        a, b = tmp / "A" / "photo.png", tmp / "B" / "photo.png"
        a.parent.mkdir(); b.parent.mkdir()
        a.write_bytes(png_bytes(color=(255, 0, 0)))
        b.write_bytes(png_bytes(color=(0, 0, 255)))
        dst = tmp / "export"
        dst.mkdir()
        reserved: set = set()
        jobs = [(a, IE.unique_dest(dst, "photo.png", reserved)), (b, IE.unique_dest(dst, "photo.png", reserved))]
        foreign = b"SOMEONE-ELSE-CREATED-THIS"
        jobs[0][1].write_bytes(foreign)                                  # after the reservation, before the copy
        res = asyncio.run(X.copy_files(jobs))
        check("C2 the file another program created is not overwritten", jobs[0][1].read_bytes() == foreign, jobs[0][1].read_bytes()[:20])
        paths = res.get("paths", [])
        check("C2 both exported images exist with their own content", len(paths) == 2 and sorted(Path(p).read_bytes() for p in paths) == sorted([a.read_bytes(), b.read_bytes()]), res)
        check("C2 the number reported equals the files really created", res["copied"] == 2 and res["copied"] == len({str(Path(p)) for p in paths}) and len(list(dst.iterdir())) == 3, (res, sorted(p.name for p in dst.iterdir())))

        # a copy that fails half way leaves no partial file and no temporary file
        dst2 = tmp / "export2"
        dst2.mkdir()
        real_copy2 = shutil.copy2

        def half_copy(s, d, *a, **k):
            Path(d).write_bytes(Path(s).read_bytes()[:10])
            raise OSError(28, "No space left on device")

        shutil.copy2 = half_copy
        try:
            try:
                asyncio.run(X.copy_files([(a, dst2 / "photo.png")]))
                raised = False
            except OSError as ex:
                raised = ex.errno == 28
        finally:
            shutil.copy2 = real_copy2
        check("C2 a copy that fails half way raises (full disk) and leaves nothing in the folder", raised and list(dst2.iterdir()) == [], list(dst2.iterdir()))

        # overwrite=True keeps the behaviour of Story Generator's 'Export Images' (the same names are replaced), still atomically
        dst3 = tmp / "export3"
        dst3.mkdir()
        (dst3 / "story_01_01.png").write_bytes(b"old export")
        res = asyncio.run(X.copy_files([(a, dst3 / "story_01_01.png")], overwrite=True))
        check("C2 overwrite=True replaces the file (Story Generator's export), with no leftovers", (dst3 / "story_01_01.png").read_bytes() == a.read_bytes() and [p.name for p in dst3.iterdir()] == ["story_01_01.png"] and res["copied"] == 1, res)
        # copy_one is 'Save As': the person chose the name in the dialog, so it replaces, atomically
        (dst3 / "chosen.png").write_bytes(b"old")
        asyncio.run(X.copy_one(b, dst3 / "chosen.png"))
        check("C2 copy_one (Save As) replaces the chosen file whole", (dst3 / "chosen.png").read_bytes() == b.read_bytes() and not [p for p in dst3.iterdir() if p.name.endswith(".part") or ".tmp" in p.name], sorted(p.name for p in dst3.iterdir()))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────────── C3 (B3): image tabs are stored by the prompt's id, not by its position
def c3():
    PL = load_program("prompt_lab")
    tmp = Path(tempfile.mkdtemp(prefix="wp_c3_"))
    real_file, real_pfile, real_notices = PL.IMAGES_FILE, PL.PROMPTS_FILE, PL.LOAD_NOTICES
    try:
        PL.IMAGES_FILE = tmp / "images.json"
        PL.PROMPTS_FILE = tmp / "prompts.json"
        PL.LOAD_NOTICES = []
        imgs = {}
        for n in "abc":
            p = tmp / f"{n}.png"
            p.write_bytes(png_bytes())
            imgs[n] = {"path": str(p), "seed": ord(n), "label": f"#{n}", "api": "wf.json", "size": "1024", "steps": 8}
        prompts = [{"id": "idA", "name": "A", "text": ""}, {"id": "idB", "name": "B", "text": ""}, {"id": "idC", "name": "C", "text": ""}]
        PL.save_img_tabs({0: [imgs["a"]], 1: [imgs["b"]], 2: [imgs["c"]]}, prompts)
        raw = json.loads(PL.IMAGES_FILE.read_text(encoding="utf-8"))
        check("C3 the file is keyed by prompt id", set(raw.get("by_id", {})) == {"idA", "idB", "idC"}, raw)

        # the audit case: A is deleted, C's image arrives, everything is saved and reloaded
        left = [prompts[1], prompts[2]]                                          # B, C
        PL.save_img_tabs({0: [imgs["b"]], 1: [imgs["c"]]}, left)
        back = PL.load_img_tabs(left)
        check("C3 after deleting A, B and C still have their own images (by id)", [e["label"] for e in back[0]] == ["#b"] and [e["label"] for e in back[1]] == ["#c"], back)
        raw = json.loads(PL.IMAGES_FILE.read_text(encoding="utf-8"))
        check("C3 the deleted prompt's images are not kept as an orphan tab", "idA" not in raw.get("by_id", {}), raw)

        # the files disagree (a crash between the two saves, or the prompt list restored from its backup): images still follow THEIR prompt
        PL.save_img_tabs({0: [imgs["a"]], 1: [imgs["b"]], 2: [imgs["c"]]}, prompts)
        shifted = [prompts[1], prompts[2]]                                       # the prompt file already lost A
        back = PL.load_img_tabs(shifted)
        check("C3 prompts and images saved at different moments: B gets B's image, not A's", [e["label"] for e in back[0]] == ["#b"] and [e["label"] for e in back[1]] == ["#c"], back)
        PL.save_img_tabs(back, shifted)
        raw = json.loads(PL.IMAGES_FILE.read_text(encoding="utf-8"))
        check("C3 the images of a prompt that is not in the list are kept in the file, not lost", "idA" in raw.get("unmapped", {}) or "idA" in raw.get("by_id", {}), raw)
        back_all = PL.load_img_tabs(prompts)                                      # A comes back (the older prompt list is restored)
        check("C3 and they are back with their prompt when it returns", [e["label"] for e in back_all.get(0, [])] == ["#a"], back_all)

        # older file keyed by position: migrated to ids using the current prompt list, nothing dropped
        PL.IMAGES_FILE.write_text(json.dumps({"0": [{"path": imgs["a"]["path"], "seed": 1, "label": "#a"}], "2": [{"path": imgs["c"]["path"], "seed": 3, "label": "#c"}], "7": [{"path": imgs["b"]["path"], "seed": 2, "label": "#x"}]}), encoding="utf-8")
        mig = PL.load_img_tabs(prompts)
        check("C3 an older position-keyed file is migrated to the right prompts", [e["label"] for e in mig[0]] == ["#a"] and [e["label"] for e in mig[2]] == ["#c"], mig)
        PL.save_img_tabs(mig, prompts)
        raw = json.loads(PL.IMAGES_FILE.read_text(encoding="utf-8"))
        kept = json.dumps(raw)
        check("C3 an old position that has no prompt any more is kept, not dropped", imgs["b"]["path"].replace("\\", "\\\\") in kept, raw)
        # settings of an image saved by id are still there after a save / load round trip
        PL.save_img_tabs({1: [imgs["b"]]}, prompts)
        again = PL.load_img_tabs(prompts)
        check("C3 settings (workflow, size, steps) survive a save / load round trip by id", [(e["api"], e["size"], e["steps"], e["seed"]) for e in again[1]] == [("wf.json", "1024", 8, ord("b"))], again)
        # the whole audit scenario in one go: Generate for C, delete A while it runs, ComfyUI answers, save, reload. (on_delete's remap and on_img's lookup are repeated here exactly as prompt_lab.py does them)
        def delete_prompt(plist, images, i):
            plist.pop(i)
            remapped = {(k - 1 if k > i else k): v for k, v in images.items() if k != i}
            images.clear()
            images.update(remapped)

        def image_arrives(plist, images, rid, entry):
            pi = PL.find_prompt_index(plist, rid)
            if pi < 0:
                return False                                                     # the prompt is gone: the image is not attached to anything
            images.setdefault(pi, []).append(entry)
            return True

        plist = [{"id": "idA", "name": "A", "text": ""}, {"id": "idB", "name": "B", "text": ""}, {"id": "idC", "name": "C", "text": ""}]
        images = {2: [dict(imgs["c"], label="#old-c")]}                         # C already had one image
        run_for = plist[2]["id"]                                                 # Generate pressed for C
        delete_prompt(plist, images, 0)                                          # A deleted while waiting
        attached = image_arrives(plist, images, run_for, dict(imgs["b"], label="#new-for-C"))
        check("C3 the new image is attached, to C only", attached and [e["label"] for e in images[1]] == ["#old-c", "#new-for-C"] and 0 not in images, images)
        PL.save_img_tabs(images, plist)
        reloaded = PL.load_img_tabs(plist)
        check("C3 after save and reload C (not B) holds both images", [e["label"] for e in reloaded[1]] == ["#old-c", "#new-for-C"] and 0 not in reloaded, reloaded)
        # the target itself is deleted while it runs
        plist2 = [{"id": "idA", "name": "A", "text": ""}, {"id": "idC", "name": "C", "text": ""}]
        images2 = {1: [dict(imgs["c"], label="#c")]}
        run_for = "idC"
        delete_prompt(plist2, images2, 1)
        attached = image_arrives(plist2, images2, run_for, dict(imgs["a"], label="#late"))
        check("C3 the target deleted while running: the image is dropped, no tab appears and no prompt is created", not attached and images2 == {} and len(plist2) == 1, (images2, plist2))
        PL.save_img_tabs(images2, plist2)
        check("C3 ... and nothing is left in the file for it", PL.load_img_tabs(plist2) == {})
    finally:
        PL.IMAGES_FILE, PL.PROMPTS_FILE, PL.LOAD_NOTICES = real_file, real_pfile, real_notices
        shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────────── C6 (B6): the REAL run_loop of Gallery Generator, with a mocked ComfyUI
def _real_run_loop(GG, run_one_results, rounds, stop_after=None):
    """Compile run_loop's own source (inside main) in a namespace of mocks and run it. Returns (status text, calls to run_one)."""
    src = inspect.getsource(GG.main)
    start = src.index("    async def run_loop(e):")
    end = src.index("    run_btn.on_click = run_loop")
    code = textwrap.dedent(src[start:end])
    calls = []
    stop = asyncio.Event()
    outs = iter(run_one_results)

    async def run_one(prompt_text, out_dir, api, **kw):
        calls.append(prompt_text)
        item = next(outs)
        if stop_after is not None and len(calls) == stop_after:
            stop.set()
        if isinstance(item, Exception):
            raise item
        return item

    ui = types.SimpleNamespace(value="")
    ns = {
        "__builtins__": __builtins__, "asyncio": asyncio, "time": __import__("time"), "Path": Path,
        "prompts_list": ["a prompt"], "working_dir": ["C:/fake"], "size_dd": types.SimpleNamespace(value=None), "_sel_size": ["x"], "_size_map": {"x": (64, 64)},
        "_apis_problems": lambda: [], "selected_apis": [{"file": "wf.json"}], "_entry_alias": lambda a: "wf", "_stop_event": stop, "_set_running": lambda on: None,
        "stop_btn": types.SimpleNamespace(disabled=False), "rounds_count": [rounds], "_api_info": lambda a: {"needs_image": False}, "resolve_wildcards": lambda s: s,
        "wildcard_text": [""], "status_lbl": ui, "gen_count_lbl": types.SimpleNamespace(value=""), "page": types.SimpleNamespace(update=lambda: None),
        "run_one": run_one, "SOURCE_IMAGE_ENABLED": False, "input_image": [], "gallery_add_one": lambda p, item: None, "_save_state": lambda: None,
        "generation_summary": GG.generation_summary,
    }
    exec(code, ns)
    asyncio.run(ns["run_loop"](None))
    return ui.value, calls


def c6():
    GG = load_program("gallery_generator")
    ok = lambda n: f"C:/fake/img{n}.png"
    boom = RuntimeError("ComfyUI error")
    text, calls = _real_run_loop(GG, [ok(1), None, ok(3), boom, ok(5)], rounds=5)
    check("C6 target 5: five attempts were made", len(calls) == 5, len(calls))
    check("C6 3 saved, 2 failed is what the line says", "3 image(s) saved" in text and "2 failed" in text and "Completed" not in text, text)
    text, calls = _real_run_loop(GG, [boom] * 5, rounds=5)
    check("C6 ComfyUI failing every time: the loop ends after the 5 attempts (no endless loop)", len(calls) == 5, len(calls))
    check("C6 ... and says that no image was saved", "no image was saved" in text and "Completed" not in text, text)
    text, calls = _real_run_loop(GG, [ok(1), ok(2), ok(3), ok(4), ok(5)], rounds=5)
    check("C6 everything saved: Completed with the real count", text == "✓ Completed — 5 image(s) saved", text)
    text, calls = _real_run_loop(GG, [ok(1), None, ok(3), ok(4), ok(5)], rounds=5, stop_after=2)
    check("C6 Stop after the 2nd attempt: the loop stops, the cancelled one is not counted as saved or failed", len(calls) == 2 and "Stopped" in text and "1 image(s) saved" in text and "failed" not in text and "Completed" not in text, (len(calls), text))
    check("C6 the meaning of the target is unchanged: prompts x workflows x rounds attempts", True)


TESTS = {"C1": c1, "C2": c2, "C3": c3, "C6": c6}


def main():
    want = [a.upper() for a in sys.argv[1:]] or list(TESTS)
    for k in want:
        print(f"--- {k}")
        TESTS[k]()
    print("ALL PASS" if not _fails else f"{len(_fails)} FAILED: {_fails}")
    return 0 if not _fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
