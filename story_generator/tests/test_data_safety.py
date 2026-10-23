"""Data-safety regressions (audit of v0.1.9): no output overwritten, no prompt overwritten, files written whole, image bytes match their extension. Temp dirs and mocks only; no real user data, no ComfyUI.

Run:  python -m story_generator.tests.test_data_safety            (all)
      python -m story_generator.tests.test_data_safety B1 B2      (only these)
"""
import asyncio
import importlib.util
import json
import os
import shutil
import struct
import sys
import tempfile
import zlib
from pathlib import Path

import flet as ft

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_fails: list = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


def load_program(stem: str):
    """Import a program file without starting its window (the programs call ft.app at the end of the file)."""
    real = ft.app
    ft.app = lambda *a, **k: None
    try:
        spec = importlib.util.spec_from_file_location(f"{stem}_under_test", ROOT / f"{stem}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        ft.app = real


def png_bytes(w=2, h=2, color=(200, 30, 30)) -> bytes:
    raw = b"".join(b"\x00" + bytes(color) * w for _ in range(h))
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def jpeg_bytes() -> bytes:
    from io import BytesIO
    from PIL import Image
    b = BytesIO(); Image.new("RGB", (4, 4), (10, 200, 10)).save(b, format="JPEG"); return b.getvalue()


def webp_bytes() -> bytes:
    from io import BytesIO
    from PIL import Image
    b = BytesIO(); Image.new("RGB", (4, 4), (10, 10, 200)).save(b, format="WEBP"); return b.getvalue()


# ───────────────────────────────────────────────────────────────────────── B1: Batch Editor output overwrite
def b1():
    BE = load_program("batch_editor")
    tmp = Path(tempfile.mkdtemp(prefix="wp_b1_"))
    try:
        (tmp / "in").mkdir()
        a, b = tmp / "in" / "cat.png", tmp / "in" / "cat.webp"          # same stem, different extension
        a.write_bytes(png_bytes()); b.write_bytes(webp_bytes())
        out = tmp / "out"
        results = iter([png_bytes(color=(1, 1, 1)), png_bytes(color=(2, 2, 2)), png_bytes(color=(3, 3, 3))])
        seq = []

        async def fake_upload(*a, **k): ...
        BE.comfy_upload_image = lambda p: "up.png"
        BE.build_workflow = lambda *a, **k: ({}, {})
        BE.comfy_queue = lambda wf, cid: "pid"
        BE.comfy_history = lambda pid: {"pid": {"outputs": {"9": {"images": [{"filename": "x.png", "subfolder": "", "type": "output"}]}}}}
        BE.comfy_get_image = lambda *a, **k: next(results)

        async def go():
            async def _sleep(_): return None
            real_sleep = asyncio.sleep
            asyncio.sleep = _sleep                                   # the job polls every 2 s: not in a test
            try:
                for p in (a, b, a):                                  # cat.png, cat.webp, then cat.png AGAIN into the same folder
                    seq.append(await BE.run_comfy_job(str(p), "prompt", out))
            finally:
                asyncio.sleep = real_sleep
        asyncio.run(go())
        check("B1 every job returns its own output path", len(set(seq)) == 3 and all(seq), seq)
        files = sorted(p.name for p in out.iterdir())
        check("B1 three outputs exist on disk (nothing overwritten)", len(files) == 3, files)
        contents = sorted(Path(p).read_bytes() for p in seq)
        check("B1 each file holds the result of ITS job", contents == sorted([png_bytes(color=(1, 1, 1)), png_bytes(color=(2, 2, 2)), png_bytes(color=(3, 3, 3))]), "contents differ")
        check("B1 the first output was not changed by the later jobs", Path(seq[0]).read_bytes() == png_bytes(color=(1, 1, 1)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────────── B2: Image Editor export collision
def b2():
    IE = load_program("image_editor")
    import export_util as X
    tmp = Path(tempfile.mkdtemp(prefix="wp_b2_"))
    try:
        (tmp / "A").mkdir(); (tmp / "B").mkdir(); dst = tmp / "export"; dst.mkdir()
        fa, fb = tmp / "A" / "photo.png", tmp / "B" / "photo.png"
        fa.write_bytes(png_bytes(color=(255, 0, 0))); fb.write_bytes(png_bytes(color=(0, 0, 255)))
        # the way the Save-selected job list is prepared: all destinations first, then the copies
        reserved: set = set()
        jobs = []
        for src in (fa, fb):
            jobs.append((src, IE.unique_dest(dst, src.name, reserved)))
        asyncio.run(X.copy_files(jobs))
        names = sorted(p.name for p in dst.iterdir())
        check("B2 two same-named images give two different files", len(names) == 2, names)
        check("B2 each file holds the content of its own source", sorted(p.read_bytes() for p in dst.iterdir()) == sorted([fa.read_bytes(), fb.read_bytes()]))
        # and an earlier export already on the disk is never touched
        (dst / "photo.png").read_bytes()
        before = {p.name: p.read_bytes() for p in dst.iterdir()}
        reserved2: set = set()
        jobs2 = [(fa, IE.unique_dest(dst, fa.name, reserved2)), (fb, IE.unique_dest(dst, fb.name, reserved2))]
        asyncio.run(X.copy_files(jobs2))
        after = {p.name: p.read_bytes() for p in dst.iterdir()}
        check("B2 a second export adds two more files and changes none of the first", len(after) == 4 and all(after[k] == v for k, v in before.items()), sorted(after))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────────── B3: Prompt Lab, the answer goes to its own prompt
def b3():
    PL = load_program("prompt_lab")
    # older file without ids: every prompt gets its own, once, and keeps its other data
    old = [{"name": "A", "text": "a"}, {"name": "B", "text": "b", "thumbnail": "xx"}, {"name": "C", "text": "c"}]
    check("B3 prompts without an id get one (older files)", PL.ensure_prompt_ids(old) and all(p["id"] for p in old) and len({p["id"] for p in old}) == 3 and old[1]["thumbnail"] == "xx")
    ids = [p["id"] for p in old]
    check("B3 ids are stable: a second pass changes nothing", not PL.ensure_prompt_ids(old) and [p["id"] for p in old] == ids)
    dup = [{"id": "x", "name": "A", "text": ""}, {"id": "x", "name": "B", "text": ""}]
    PL.ensure_prompt_ids(dup)
    check("B3 a duplicated id is replaced", dup[0]["id"] != dup[1]["id"])

    # the audit scenario: ask about A, delete A while the LLM works, the answer arrives
    prompts = [{"name": "A", "text": "a"}, {"name": "B", "text": "b"}, {"name": "C", "text": "c"}]
    PL.ensure_prompt_ids(prompts)
    target_id = prompts[0]["id"]                  # the id captured when the question was sent
    prompts.pop(0)                                # A deleted while waiting
    outcome, i = PL.apply_llm_prompt(prompts, target_id, "LLM REWRITE OF A")
    check("B3 target deleted: the answer is dropped and says so", outcome == "target_deleted" and i == -1, (outcome, i))
    check("B3 target deleted: no other prompt was changed", [(p["name"], p["text"]) for p in prompts] == [("B", "b"), ("C", "c")], prompts)

    # ask about C (last), delete A above it: C moved to index 1, the old index 2 is out of range
    prompts = [{"name": "A", "text": "a"}, {"name": "B", "text": "b"}, {"name": "C", "text": "c"}]
    PL.ensure_prompt_ids(prompts)
    target_id = prompts[2]["id"]
    prompts.pop(0)
    outcome, i = PL.apply_llm_prompt(prompts, target_id, "LLM REWRITE OF C")
    check("B3 a prompt that moved is still found by its id", outcome == "updated" and prompts[i]["name"] == "C" and prompts[i]["text"] == "LLM REWRITE OF C", (outcome, i, prompts))
    check("B3 and no extra prompt was created, B is untouched", len(prompts) == 2 and prompts[0]["text"] == "b")

    # selection changed / another tab: the id still points at the prompt that was asked about
    prompts = [{"name": "A", "text": "a"}, {"name": "B", "text": "b"}]
    PL.ensure_prompt_ids(prompts)
    asked = prompts[1]["id"]
    selected_now = 0                              # the user clicked A meanwhile
    outcome, i = PL.apply_llm_prompt(prompts, asked, "NEW B")
    check("B3 changing the selection while waiting does not redirect the answer", outcome == "updated" and prompts[1]["text"] == "NEW B" and prompts[selected_now]["text"] == "a")

    # no prompt selected when the question was asked: a new prompt is created (the old behaviour)
    prompts = [{"name": "A", "text": "a"}]
    PL.ensure_prompt_ids(prompts)
    outcome, i = PL.apply_llm_prompt(prompts, None, "FRESH")
    check("B3 no prompt selected: the answer becomes a new prompt with its own id", outcome == "created" and prompts[i]["text"] == "FRESH" and prompts[i]["id"] and len(prompts) == 2)

    # loading a saved file adds ids and the list round-trips
    tmp = Path(tempfile.mkdtemp(prefix="wp_b3_"))
    try:
        real = PL.PROMPTS_FILE
        PL.PROMPTS_FILE = tmp / "prompts.json"
        PL.PROMPTS_FILE.write_text(json.dumps([{"name": "A", "text": "a"}, {"name": "B", "text": "b"}]), encoding="utf-8")
        loaded = PL.load_prompts()
        check("B3 load_prompts adds ids to an older file", all(p.get("id") for p in loaded) and [p["name"] for p in loaded] == ["A", "B"])
        PL.save_prompts(loaded)
        again = PL.load_prompts()
        check("B3 the ids survive save and load", [p["id"] for p in again] == [p["id"] for p in loaded])
    finally:
        PL.PROMPTS_FILE = real
        shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────────── B5: Prompt Lab, saves are atomic and a damaged file is not "empty"
def b5():
    PL = load_program("prompt_lab")
    import export_util as X
    tmp = Path(tempfile.mkdtemp(prefix="wp_b5_"))
    real_file, real_notices = PL.PROMPTS_FILE, PL.LOAD_NOTICES
    try:
        PL.PROMPTS_FILE = tmp / "prompts.json"
        PL.LOAD_NOTICES = []
        good = [{"id": "p1", "name": "A", "text": "first"}, {"id": "p2", "name": "B", "text": "second"}]
        PL.save_prompts(good)
        check("B5 a normal save writes the list", json.loads(PL.PROMPTS_FILE.read_text(encoding="utf-8")) == good)
        check("B5 no temporary file is left behind", [p.name for p in tmp.iterdir() if p.name.endswith(".tmp")] == [])

        # the swap fails (disk full, access denied...): the old file must be exactly as it was
        before = PL.PROMPTS_FILE.read_bytes()
        real_replace = os.replace
        def boom(a, b): raise OSError(28, "No space left on device")
        os.replace = boom
        try:
            try:
                PL.save_prompts([{"id": "p9", "name": "NEW", "text": "x"}])
                raised = False
            except OSError:
                raised = True
        finally:
            os.replace = real_replace
        check("B5 a failed save raises (the friendly error dialog can explain it)", raised)
        check("B5 a failed save leaves the old file byte for byte as it was", PL.PROMPTS_FILE.read_bytes() == before)
        check("B5 and removes its temporary file", [p.name for p in tmp.iterdir() if p.name.endswith(".tmp")] == [])

        # the data cannot be serialised: nothing is touched
        try:
            PL.save_prompts([{"id": "p1", "bad": object()}])
        except TypeError:
            pass
        check("B5 an unserialisable list does not damage the file", PL.PROMPTS_FILE.read_bytes() == before)

        # a second save keeps the previous good version as a backup
        PL.save_prompts(good + [{"id": "p3", "name": "C", "text": "third"}])
        bak = json.loads(Path(str(PL.PROMPTS_FILE) + ".bak").read_text(encoding="utf-8"))
        check("B5 the last good copy is kept as .bak", bak == good)

        # the file is cut in the middle (power loss, an older non-atomic writer): NOT treated as an empty list, restored from the backup, damaged copy kept
        text = PL.PROMPTS_FILE.read_text(encoding="utf-8")
        PL.PROMPTS_FILE.write_text(text[: len(text) // 2], encoding="utf-8")
        PL.LOAD_NOTICES.clear()
        loaded = PL.load_prompts()
        check("B5 a truncated file is restored from the backup, not read as empty", [p["name"] for p in loaded] == ["A", "B"], loaded)
        check("B5 the person is told", PL.LOAD_NOTICES and "backup" in PL.LOAD_NOTICES[0], PL.LOAD_NOTICES)
        kept = [p.name for p in tmp.iterdir() if ".corrupt-" in p.name]
        check("B5 the damaged file is kept under its own name", len(kept) == 1 and (tmp / kept[0]).read_text(encoding="utf-8") == text[: len(text) // 2], kept)
        PL.save_prompts(loaded)                                          # the next normal save must not destroy the evidence
        check("B5 a later save does not touch the kept damaged copy", (tmp / kept[0]).exists())

        # damaged and NO backup: starts empty, says so, keeps the file
        PL.PROMPTS_FILE.write_text("{ not json", encoding="utf-8")
        os.unlink(str(PL.PROMPTS_FILE) + ".bak")
        PL.LOAD_NOTICES.clear()
        check("B5 damaged with no backup: starts empty", PL.load_prompts() == [])
        check("B5 ... but says so and keeps the damaged file", PL.LOAD_NOTICES and "no backup" in PL.LOAD_NOTICES[0] and len([p for p in tmp.iterdir() if ".corrupt-" in p.name]) >= 2, PL.LOAD_NOTICES)

        # no file at all is a normal first start: no warning
        os.unlink(PL.PROMPTS_FILE)
        PL.LOAD_NOTICES.clear()
        check("B5 a missing file is a normal first start (no warning)", PL.load_prompts() == [] and PL.LOAD_NOTICES == [])
    finally:
        PL.PROMPTS_FILE, PL.LOAD_NOTICES = real_file, real_notices
        shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────────── B7: Prompt Lab, image tabs keep their settings
def b7():
    PL = load_program("prompt_lab")
    tmp = Path(tempfile.mkdtemp(prefix="wp_b7_"))
    real_file, real_notices = PL.IMAGES_FILE, PL.LOAD_NOTICES
    try:
        PL.IMAGES_FILE = tmp / "images.json"
        PL.LOAD_NOTICES = []
        img1, img2 = tmp / "a.png", tmp / "b.png"
        img1.write_bytes(png_bytes()); img2.write_bytes(png_bytes(color=(9, 9, 9)))
        entries = {0: [{"path": str(img1), "seed": 12345, "label": "#1 10:00", "api": "krea2.json", "size": "[16:9] 2304 x 1296", "steps": 8},
                       {"path": str(img2), "seed": 777, "label": "⚡#2 10:05", "api": "krea2.json", "size": "[1:1] 1024 x 1024", "steps": 4}]}
        prompts = [{"id": "p0", "name": "A", "text": ""}, {"id": "p1", "name": "B", "text": ""}]      # the image tabs are saved by prompt id
        PL.save_img_tabs(entries, prompts)
        back = PL.load_img_tabs(prompts)
        check("B7 path, seed and label survive a restart", [(e["path"], e["seed"], e["label"]) for e in back[0]] == [(e["path"], e["seed"], e["label"]) for e in entries[0]], back)
        check("B7 workflow, size and steps survive a restart", [(e["api"], e["size"], e["steps"]) for e in back[0]] == [("krea2.json", "[16:9] 2304 x 1296", 8), ("krea2.json", "[1:1] 1024 x 1024", 4)], back)
        # an older file (only path / seed / label) still loads, with the missing settings simply absent
        PL.IMAGES_FILE.write_text(json.dumps({"0": [{"path": str(img1), "seed": 5, "label": "#1"}], "1": [{"path": str(img2)}]}), encoding="utf-8")
        old = PL.load_img_tabs(prompts)
        check("B7 an older file without settings loads", old[0][0]["seed"] == 5 and old[0][0].get("api") is None, old)
        check("B7 an entry without seed / label gets defaults the screen can read", old[1][0]["seed"] == -1 and old[1][0]["label"])
        # entries whose image file is gone are skipped, the others stay
        img2.unlink()
        gone = PL.load_img_tabs(prompts)
        check("B7 a missing image file is skipped", 1 not in gone and 0 in gone)
        # a damaged file is not 'no images': kept and reported
        PL.IMAGES_FILE.write_text("{ cut", encoding="utf-8")
        PL.LOAD_NOTICES.clear()
        check("B7 a damaged image list starts empty, says so and keeps the file", PL.load_img_tabs(prompts) == {} and PL.LOAD_NOTICES and any(".corrupt-" in p.name for p in tmp.iterdir()), PL.LOAD_NOTICES)
    finally:
        PL.IMAGES_FILE, PL.LOAD_NOTICES = real_file, real_notices
        shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────────── B4: image bytes, extension and MIME agree (Batch Editor, Gallery Generator)
def b4():
    import export_util as X
    check("B4 sniff: PNG / JPEG / WebP are recognised from their bytes", [X.image_ext(png_bytes()), X.image_ext(jpeg_bytes()), X.image_ext(webp_bytes())] == [".png", ".jpg", ".webp"])
    check("B4 sniff: MIME types match", [X.image_mime(png_bytes()), X.image_mime(jpeg_bytes()), X.image_mime(webp_bytes())] == ["image/png", "image/jpeg", "image/webp"])
    check("B4 unknown bytes: the extension fallback decides the MIME type, jpeg when unknown", X.image_mime(b"junk", ".png") == "image/png" and X.image_mime(b"junk", ".xyz") == "image/jpeg" and X.image_ext(b"junk") == ".jpg")

    GG = load_program("gallery_generator")
    BE = load_program("batch_editor")
    tmp = Path(tempfile.mkdtemp(prefix="wp_b4_"))
    try:
        # Gallery run_one: ComfyUI returns PNG bytes -> the file must be .png and hold exactly those bytes; a second image never overwrites the first
        payloads = iter([png_bytes(color=(5, 5, 5)), png_bytes(color=(6, 6, 6)), jpeg_bytes(), webp_bytes()])
        real_gallery_upload = GG.comfy_upload_image
        GG.comfy_upload_image = lambda p: "up.png"
        GG.build_workflow = lambda *a, **k: {}
        GG.comfy_queue = lambda wf, cid: "pid"
        GG.comfy_history = lambda pid: {"pid": {"outputs": {"9": {"images": [{"filename": "x", "subfolder": "", "type": "output"}]}}}}
        GG.comfy_get_image = lambda *a, **k: next(payloads)
        got = []

        async def go():
            async def _sleep(_): return None
            real_sleep = asyncio.sleep
            asyncio.sleep = _sleep
            try:
                for _ in range(4):
                    got.append(await GG.run_one("p", tmp / "gal", "wf.json"))
            finally:
                asyncio.sleep = real_sleep
        asyncio.run(go())
        exts = [Path(p).suffix for p in got]
        check("B4 gallery: the extension follows the real image format", exts == [".png", ".png", ".jpg", ".webp"], exts)
        check("B4 gallery: every file holds exactly the bytes that were received", [Path(p).read_bytes() for p in got] == [png_bytes(color=(5, 5, 5)), png_bytes(color=(6, 6, 6)), jpeg_bytes(), webp_bytes()])
        check("B4 gallery: four different files", len(set(got)) == 4)
        # preview MIME comes from the bytes: an older PNG saved as .jpg is still shown as PNG
        old = tmp / "old.jpg"
        old.write_bytes(png_bytes())
        check("B4 gallery preview: a PNG named .jpg gets the PNG MIME type", GG._to_src(str(old)).startswith("data:image/png;base64,"), GG._to_src(str(old))[:40])
        good = tmp / "good.webp"
        good.write_bytes(webp_bytes())
        check("B4 gallery preview: a WebP file gets the WebP MIME type", GG._to_src(str(good)).startswith("data:image/webp;base64,"))
        # the upload to ComfyUI states the real type too
        sent = {}
        class _R:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"name": "up.png"}'
        real_open = GG.urllib.request.urlopen
        GG.urllib.request.urlopen = lambda req, timeout=0: (sent.update(body=req.data), _R())[1]
        try:
            real_gallery_upload(str(old))
        finally:
            GG.urllib.request.urlopen = real_open
        check("B4 gallery upload: Content-Type of a PNG is image/png", b"Content-Type: image/png" in sent["body"], sent["body"][:200])
        # Batch Editor: same bytes -> same extension (run_comfy_job), checked with real PNG, JPEG and WebP results
        results = iter([png_bytes(), jpeg_bytes(), webp_bytes()])
        BE.comfy_upload_image = lambda p: "up.png"
        BE.build_workflow = lambda *a, **k: ({}, {})
        BE.comfy_queue = lambda wf, cid: "pid"
        BE.comfy_history = lambda pid: {"pid": {"outputs": {"9": {"images": [{"filename": "x", "subfolder": "", "type": "output"}]}}}}
        BE.comfy_get_image = lambda *a, **k: next(results)
        outs = []

        async def go2():
            async def _sleep(_): return None
            real_sleep = asyncio.sleep
            asyncio.sleep = _sleep
            try:
                for name in ("a.png", "b.png", "c.png"):
                    (tmp / name).write_bytes(png_bytes())
                    outs.append(await BE.run_comfy_job(str(tmp / name), "p", tmp / "bat"))
            finally:
                asyncio.sleep = real_sleep
        asyncio.run(go2())
        check("B4 batch: PNG, JPEG and WebP results get .png, .jpg and .webp", [Path(p).suffix for p in outs] == [".png", ".jpg", ".webp"], outs)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────────── B6: Gallery Generator, what the final line says
def b6():
    GG = load_program("gallery_generator")
    s = GG.generation_summary
    five = s(5, 3, 2, False, 5)
    check("B6 5 started, 3 saved, 2 failed: says so and is not 'Completed'", "3 image(s) saved" in five and "2 failed" in five and "Completed" not in five, five)
    check("B6 everything saved: Completed with the real count", s(5, 5, 0, False, 5) == "✓ Completed — 5 image(s) saved")
    check("B6 nothing saved: not 'Completed'", "Completed" not in s(3, 0, 3, False, 3) and "no image was saved" in s(3, 0, 3, False, 3))
    check("B6 no failure but nothing came back: not 'Completed'", "Completed" not in s(2, 0, 0, False, 2))
    stopped = s(3, 2, 0, True, 10)
    check("B6 stopped: shows saved and started, no 'Completed'", "Stopped" in stopped and "2 image(s) saved" in stopped and "3 of 10" in stopped and "Completed" not in stopped, stopped)
    check("B6 stopped with a failure keeps the failure", "1 failed" in s(4, 2, 1, True, 10))
    # the loop's counters: read the source of run_loop to be sure they are the ones the summary gets (a full run needs a window and ComfyUI)
    import inspect
    src = inspect.getsource(GG.main)
    check("B6 the loop counts a success only after the image is listed, and a failure on error or no image", "succeeded += 1" in src and src.index("gallery_add_one(base_prompt, new_item)") < src.index("succeeded += 1") and "failed += 1" in src and "generation_summary(n, succeeded, failed, stopped, total)" in src)


# ───────────────────────────────────────────────────────────────────────── B8: make_release scans the whole staged tree and never prints a key whole
def b8():
    spec = importlib.util.spec_from_file_location("make_release_under_test", ROOT / "make_release.py")
    MR = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(MR)
    tmp = Path(tempfile.mkdtemp(prefix="wp_b8_"))
    try:
        # fake secrets are built at run time: this test file itself holds no key-shaped text
        xai = "xai-" + "A1b2C3d4" * 5
        sk = "sk-" + "Z9y8X7w6" * 4
        bearer = "Bearer " + "q" * 30
        assigned = 'api_key = "' + "k3Y" * 10 + '"'
        dest = tmp / "WorkflowPad-test"
        (dest / "_internal" / "lib" / "data").mkdir(parents=True)
        (dest / "licenses").mkdir()
        (dest / "docs.html").write_text(f"<p>{xai}</p>", encoding="utf-8")                                                  # .html: was never scanned
        (dest / "_internal" / "lib" / "data" / "conf.yaml").write_text(f"token: {bearer}\n", encoding="utf-8")            # in the staged tree, not copied by stage()
        (dest / "_internal" / "lib" / "mod.py").write_text(f'KEY = "{sk}"\n', encoding="utf-8")                           # library source: keys only
        (dest / "_internal" / "lib" / "notes.txt").write_text(r"see C:\Users\bob\Desktop\x" + "\n", encoding="utf-8")     # a build-machine path in a text file
        (dest / "_internal" / "lib" / "paths.py").write_text(r'DOC = "C:\Users\somebody\docs"' + "\n", encoding="utf-8")   # a path inside a LIBRARY's own source: not a leak
        (dest / "settings.cfg").write_text(assigned + "\n", encoding="utf-8")
        (dest / "licenses" / "L.txt").write_text(r"C:\Users\x" + "\n", encoding="utf-8")                                    # third-party license texts are skipped
        (dest / "core.dll").write_bytes(b"MZ\x00\x00" + xai.encode())                                                        # a binary is never read
        problems = MR.scan_tree(dest)
        text = "\n".join(problems)
        check("B8 a key in an .html file is found", "docs.html" in text and "xai key" in text, text)
        check("B8 a token in a staged library data file (not copied by stage()) is found", "conf.yaml" in text and "bearer token" in text, text)
        check("B8 a key in a bundled .py source is found", "mod.py" in text and "openai-style key" in text, text)
        check("B8 an assigned api key in a .cfg is found", "settings.cfg" in text and "assigned api key" in text, text)
        check("B8 a build-machine path in a text file is found", "notes.txt" in text and "Users" in text, text)
        check("B8 a path inside a library's .py is NOT reported (no false alarm)", "paths.py" not in text, text)
        check("B8 license texts are skipped and a binary is never read", "L.txt" not in text and "core.dll" not in text, text)
        check("B8 no key or token is printed whole", not any(k in text for k in (xai, sk, bearer, "k3Y" * 10)) and "hidden" in text, text)
        check("B8 the old scan of the 'copied' list still works", MR.scan([dest / "docs.html"], dest) != [])
        # a clean tree is clean
        clean = tmp / "clean"
        (clean / "_internal").mkdir(parents=True)
        (clean / "README.txt").write_text("Put your workflows in comfy_apis. https://github.com/sudja-tongtip/WorkflowPad\n", encoding="utf-8")
        (clean / "_internal" / "a.py").write_text("import os\nPATH = os.environ['HOME']\n", encoding="utf-8")
        check("B8 a clean tree gives no problem", MR.scan_tree(clean) == [], MR.scan_tree(clean))
        # personal data files must not be packaged
        bad_names = ["update_check.json", "prompts_tester.json", "prompt_images.json", "credentials.json", "saved_story.json", "llm_chat_config.json", ".env", "id_rsa", "session.log", "m.pt"]
        check("B8 user-data and credential file names are forbidden in the release", all(MR.FORBIDDEN_NAME.search(n) for n in bad_names), [n for n in bad_names if not MR.FORBIDDEN_NAME.search(n)])
        check("B8 ordinary library files are not forbidden (cacert.pem, pyc, yaml)", not any(MR.FORBIDDEN_NAME.search(n) for n in ("cacert.pem", "a.pyd", "config.yaml", "update.json")))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


TESTS = {"B1": b1, "B2": b2, "B3": b3, "B4": b4, "B5": b5, "B6": b6, "B7": b7, "B8": b8}


def main():
    want = [a.upper() for a in sys.argv[1:]] or list(TESTS)
    for k in want:
        print(f"--- {k}")
        TESTS[k]()
    print("ALL PASS" if not _fails else f"{len(_fails)} FAILED: {_fails}")
    return 0 if not _fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
