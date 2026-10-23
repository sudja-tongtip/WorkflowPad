"""Prompt Lab 0.1.9 bug fixes B1-B5. Temp dirs and mocks only; no user data, no ComfyUI, no LLM.

Run:  python -m story_generator.tests.test_prompt_lab_bugfix
"""
import asyncio
import inspect
import json
import shutil
import sys
import tempfile
import textwrap
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from story_generator.tests.test_data_safety import check, load_program, png_bytes, jpeg_bytes, webp_bytes, _fails  # noqa: E402

PL = load_program("prompt_lab")


def _nested(name: str, ns: dict):
    """Compile ONE nested function of main() from the program's own source (so the real code is tested) into `ns`, where its free names are mocks."""
    src = inspect.getsource(PL.main)
    start = src.index(f"    def {name}(") if f"    def {name}(" in src else src.index(f"    async def {name}(")
    rest = src[start:]
    end = len(rest)
    for marker in ("\n    def ", "\n    async def ", "\n    btn_", "\n    # "):
        k = rest.find(marker, 10)
        if k != -1:
            end = min(end, k)
    exec(compile(textwrap.dedent(rest[:end]), f"<{name}>", "exec"), ns)
    return ns[name]


# ───────────────────────────────────────────────────────────────────────────────────────── B1
def b1():
    tmp = Path(tempfile.mkdtemp(prefix="wp_pl_b1_"))
    saved = (PL.PROMPTS_FILE, PL.IMAGES_FILE, PL.LOAD_NOTICES, PL._IDS_UNSAVED[0])
    try:
        PL.PROMPTS_FILE, PL.IMAGES_FILE, PL.LOAD_NOTICES = tmp / "prompts.json", tmp / "images.json", []
        PL._IDS_UNSAVED[0] = False
        img_a, img_b = tmp / "a.png", tmp / "b.png"
        img_a.write_bytes(png_bytes()); img_b.write_bytes(png_bytes(3, 3))
        legacy = [{"name": "A", "text": "prompt a"}, {"name": "B", "text": "prompt b"}]
        PL.PROMPTS_FILE.write_text(json.dumps(legacy), encoding="utf-8")                      # an older file: no ids

        prompts = PL.load_prompts()
        on_disk = json.loads(PL.PROMPTS_FILE.read_text(encoding="utf-8"))
        check("B1 ids made at load are written to the prompt file", all(p.get("id") for p in on_disk) and [p["id"] for p in on_disk] == [p["id"] for p in prompts], on_disk)
        check("B1 the old file is kept as a backup", json.loads(Path(str(PL.PROMPTS_FILE) + ".bak").read_text(encoding="utf-8")) == legacy)
        PL._PROMPT_LIST[0] = prompts
        PL.save_img_tabs({0: [{"path": str(img_a), "seed": 1, "label": "#a"}], 1: [{"path": str(img_b), "seed": 2, "label": "#b"}]})

        PL.LOAD_NOTICES.clear()                                                               # restart
        prompts2 = PL.load_prompts()
        PL._PROMPT_LIST[0] = prompts2
        back = PL.load_img_tabs(prompts2)
        check("B1 after a restart the images are on the same prompts (A has a, B has b)", [e["label"] for e in back[0]] == ["#a"] and [e["label"] for e in back[1]] == ["#b"], back)
        check("B1 nothing was parked as unmapped", not json.loads(PL.IMAGES_FILE.read_text(encoding="utf-8")).get("unmapped"))

        # the prompt file cannot be written: the ids stay unsaved and NO mapping that refers to them is written
        PL.PROMPTS_FILE.write_text(json.dumps(legacy), encoding="utf-8")
        PL.IMAGES_FILE.unlink()
        real = PL._export_util.write_json_atomic

        def boom(path, obj, **kw):
            if Path(path) == PL.PROMPTS_FILE:
                raise OSError(28, "No space left on device")
            return real(path, obj, **kw)

        PL._export_util.write_json_atomic = boom
        try:
            PL.LOAD_NOTICES.clear()
            p3 = PL.load_prompts()
            check("B1 a failed write of the ids is flagged and reported", PL._IDS_UNSAVED[0] and any("could not be updated" in n for n in PL.LOAD_NOTICES), PL.LOAD_NOTICES)
            PL._PROMPT_LIST[0] = p3
            raised = False
            try:
                PL.save_img_tabs({0: [{"path": str(img_a), "seed": 1, "label": "#a"}]})
            except OSError:
                raised = True
            check("B1 no image mapping is written while the ids are not on disk", raised and not PL.IMAGES_FILE.exists())
        finally:
            PL._export_util.write_json_atomic = real
        PL.save_img_tabs({0: [{"path": str(img_a), "seed": 1, "label": "#a"}]})                # the disk works again: the ids are saved first, then the mapping
        check("B1 once the disk works the ids and the mapping are both written", not PL._IDS_UNSAVED[0] and json.loads(PL.PROMPTS_FILE.read_text(encoding="utf-8"))[0].get("id") == p3[0]["id"]
              and p3[0]["id"] in json.loads(PL.IMAGES_FILE.read_text(encoding="utf-8"))["by_id"])
    finally:
        PL.PROMPTS_FILE, PL.IMAGES_FILE, PL.LOAD_NOTICES, PL._IDS_UNSAVED[0] = saved
        shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────────────────────────── B2
def b2():
    jp, pn, wp = jpeg_bytes(), png_bytes(), webp_bytes()
    check("B2 JPEG bytes -> .jpg", PL.image_save_path("C:/x/out.png", jp).endswith("out.jpg"), PL.image_save_path("C:/x/out.png", jp))
    check("B2 WebP bytes -> .webp", PL.image_save_path("C:/x/out.png", wp).endswith("out.webp"))
    check("B2 PNG bytes keep .png", PL.image_save_path("C:/x/out.png", pn) == "C:/x/out.png")
    check("B2 a name with no extension gets the right one", PL.image_save_path("C:/x/out", jp) == "C:/x/out.jpg")
    check("B2 .jpeg is accepted for JPEG bytes", PL.image_save_path("C:/x/out.jpeg", jp) == "C:/x/out.jpeg")
    check("B2 a dotted name that is not an image extension is kept and the extension added", PL.image_save_path("C:/x/v1.2", pn) == "C:/x/v1.2.png")

    tmp = Path(tempfile.mkdtemp(prefix="wp_pl_b2_"))
    saved = (PL.TEMP_DIR, PL.comfy_queue, PL.comfy_hist, PL.comfy_get_img)
    try:
        PL.TEMP_DIR = tmp
        for label, data, ext in (("PNG", pn, ".png"), ("JPEG", jp, ".jpg"), ("WebP", wp, ".webp")):
            PL.comfy_queue = lambda wf, cid: "pid1"
            PL.comfy_hist = lambda pid: {"pid1": {"outputs": {"9": {"images": [{"filename": "f", "subfolder": "", "type": "output"}]}}}}
            PL.comfy_get_img = lambda f, s, t, _d=data: _d
            got = []

            async def on_img(path): got.append(path)
            async def noop(*a, **k): pass
            real_sleep = asyncio.sleep
            asyncio.sleep = lambda *_a, **_k: real_sleep(0)
            try:
                asyncio.run(PL.run_workflow({"1": {"class_type": "X"}}, on_img, noop, noop))
            finally:
                asyncio.sleep = real_sleep
            check(f"B2 a {label} result is stored as {ext} and the bytes are untouched", len(got) == 1 and got[0].endswith(ext) and Path(got[0]).read_bytes() == data, got)
    finally:
        PL.TEMP_DIR, PL.comfy_queue, PL.comfy_hist, PL.comfy_get_img = saved
        shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────────────────────────── B3
def b3():
    class Ctl(types.SimpleNamespace):
        pass

    tmp = Path(tempfile.mkdtemp(prefix="wp_pl_b3_"))
    try:
        files = {}
        for n in "abc":
            (tmp / f"{n}.png").write_bytes(png_bytes()); files[n] = str(tmp / f"{n}.png")
        seed_field = Ctl(value="", color=None)
        ns = dict(
            _prompt_images={0: [{"path": files["a"], "seed": 123}, {"path": files["b"], "seed": 0}, {"path": files["c"]}]},
            _show_blank=lambda: None, Path=Path, _img_bytes=[b""], _zoom=[1.0], _pan_x=[0.0], _pan_y=[0.0],
            preview_img=Ctl(src=None, visible=False, page=None), img_wrapper=Ctl(scale=None, offset=None), preview_ph=Ctl(visible=True),
            btn_save=Ctl(disabled=True), seed_field=seed_field, _current_seed=[-1], img_tab_info=Ctl(value=""),
            C={"amber": "AMBER", "text2": "TEXT2"}, ft=types.SimpleNamespace(Scale=lambda v: v, Offset=lambda x, y: (x, y)), page=types.SimpleNamespace(update=lambda: None),
        )
        show = _nested("_show_tab", ns)
        show(0, 0)
        check("B3 seed 123 is shown, kept as the generation seed and named in the image info", seed_field.value == "123" and ns["_current_seed"][0] == 123 and "seed 123" in ns["img_tab_info"].value, ns["img_tab_info"].value)
        show(0, 1)
        check("B3 seed 0 is a seed: shown, kept and named in the image info", seed_field.value == "0" and ns["_current_seed"][0] == 0 and "seed 0" in ns["img_tab_info"].value, (seed_field.value, ns["_current_seed"], ns["img_tab_info"].value))
        show(0, 2)
        check("B3 an image with no seed does not show the previous image's seed as its own", "seed" not in ns["img_tab_info"].value, ns["img_tab_info"].value)
        check("B3 ...and does not clear the generation seed (field == _current_seed)", seed_field.value == "0" and ns["_current_seed"][0] == 0, (seed_field.value, ns["_current_seed"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────────────────────────── B4
def _run_apis_env(tmp: Path, prompts: list, fake_run_workflow):
    class Ctl(types.SimpleNamespace):
        pass

    statuses = []
    ns = dict(
        editor_field=Ctl(value="a prompt"), sel_apis=["wf.json"], API_DIR=tmp, size_dd=Ctl(value=None), _size_map={}, _DEFAULT_SIZE="[16:9] 2304 × 1296", steps_dd=Ctl(value="8"),
        prompts=prompts, sel=[0], btn_new_run=Ctl(disabled=False), btn_seed_run=Ctl(disabled=False), btn_quick=Ctl(disabled=False), _gen_state=lambda r: None,
        progress=Ctl(visible=False), set_status=lambda m, c=None: statuses.append(m), page=Ctl(update=lambda: None), _stop_event=asyncio.Event(),
        _current_seed=[-1], seed_field=Ctl(value="", color=None), C={k: k for k in ("amber", "accent", "text2", "green", "red")}, _prompt_images={},
        save_img_tabs=lambda *a, **k: None, make_thumbnail_bytes=lambda b: b, save_prompts=lambda p: None, load_thumb_for_prompt=lambda i: None,
        _show_tab=lambda a, b: None, _active_img_tab=[0], _rebuild_tabs=lambda: None, run_workflow=fake_run_workflow,
        inject_prompt=PL.inject_prompt, inject_size=PL.inject_size, inject_seed=PL.inject_seed, find_prompt_index=PL.find_prompt_index,
        _LATENT_CLS=PL._LATENT_CLS, _SCLS=PL._SCLS, base64=PL.base64, time=PL.time, json=json, Path=Path, asyncio=asyncio,
    )
    return ns, statuses


def b4():
    tmp = Path(tempfile.mkdtemp(prefix="wp_pl_b4_"))
    try:
        (tmp / "wf.json").write_text(json.dumps({"1": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 5}}}), encoding="utf-8")
        img = tmp / "r.png"; img.write_bytes(png_bytes())
        prompts = [{"id": "A", "name": "A", "text": "a"}, {"id": "B", "name": "B", "text": "b"}]
        saved_to = []

        async def fake(wf, on_img, on_err, on_done, stop_event=None, on_stop=None):
            del prompts[0]                       # A is deleted while ComfyUI is working
            await on_img(str(img))
            await on_done()

        ns, statuses = _run_apis_env(tmp, prompts, fake)
        ns["save_img_tabs"] = lambda *a, **k: saved_to.append(a)
        run = _nested("_run_apis", ns)
        asyncio.run(run("new"))
        last = statuses[-1]
        check("B4 the image is not attached to any prompt (not to B)", ns["_prompt_images"] == {} and not saved_to, ns["_prompt_images"])
        check("B4 no prompt was created in its place", [p["id"] for p in prompts] == ["B"])
        check("B4 the result is reported as 'prompt deleted', not as Done", "deleted" in last and "Done" not in last, last)
        check("B4 the buttons are usable again", not ns["btn_new_run"].disabled and not ns["progress"].visible)

        prompts[:] = [{"id": "A", "name": "A", "text": "a"}, {"id": "B", "name": "B", "text": "b"}]       # control: nothing deleted
        async def fake_ok(wf, on_img, on_err, on_done, stop_event=None, on_stop=None):
            await on_img(str(img)); await on_done()
        ns, statuses = _run_apis_env(tmp, prompts, fake_ok)
        run = _nested("_run_apis", ns)
        asyncio.run(run("new"))
        check("B4 control: with no deletion the image is attached to the prompt and the run is Done", len(ns["_prompt_images"].get(0, [])) == 1 and statuses[-1].startswith("✅ Done"), (ns["_prompt_images"], statuses[-1]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ───────────────────────────────────────────────────────────────────────────────────────── B5
def b5():
    src = inspect.getsource(PL.main)
    tail = src[src.rindex("_update_check.start_notice"):]
    check("B5 on start the newest prompt is selected (through the same code as a click)", "_select(len(prompts) - 1)" in tail and tail.index("if prompts:") < tail.index("_select(len(prompts) - 1)"))
    check("B5 with no prompts nothing is created", "on_new(" not in tail and "prompts.append" not in tail)


# ───────────────────────────────────────────────────────────────────────────────────────── final cleanup (round 3)
async def _async_value(v):
    return v


def f1_save_as_collision():
    class Ctl(types.SimpleNamespace):
        pass

    tmp = Path(tempfile.mkdtemp(prefix="wp_pl_f1_"))
    try:
        wp = webp_bytes()
        existing = tmp / "photo.webp"
        existing.write_bytes(b"MY-OTHER-PHOTO")
        statuses = []
        picker = Ctl()
        picker.save_file = lambda **kw: _async_value(str(tmp / "photo.png"))          # the person typed photo.png
        ns = dict(_img_bytes=[wp], _BLANK=b"x", ft=types.SimpleNamespace(FilePicker=lambda: picker), _export_util=PL._export_util, image_save_path=PL.image_save_path,
                  time=PL.time, Path=Path, set_status=lambda m, c=None: statuses.append(m), C={"green": "g"})
        save = _nested("on_save_click", ns)
        asyncio.run(save(None))
        check("F1 the other file photo.webp is NOT overwritten", existing.read_bytes() == b"MY-OTHER-PHOTO", existing.read_bytes()[:20])
        saved = sorted(p.name for p in tmp.iterdir() if p.name != "photo.webp")
        check("F1 the image is saved under the next free name with the extension of its bytes", saved == ["photo_1.webp"] and (tmp / "photo_1.webp").read_bytes() == wp, saved)
        check("F1 the status says which name was used", bool(statuses) and "photo_1.webp" in statuses[-1], statuses)

        (tmp / "photo_1.webp").unlink()
        existing.unlink()
        asyncio.run(save(None))
        check("F1 with no collision it is saved as photo.webp", (tmp / "photo.webp").read_bytes() == wp)

        # a failed write never destroys the file that was there
        (tmp / "photo.webp").write_bytes(b"KEEP")
        picker.save_file = lambda **kw: _async_value(str(tmp / "photo.webp"))
        real = PL._export_util.os.replace

        def failing_replace(a, b):
            raise OSError(28, "No space left on device")

        PL._export_util.os.replace = failing_replace
        raised = False
        try:
            asyncio.run(save(None))
        except OSError:
            raised = True
        finally:
            PL._export_util.os.replace = real
        check("F1 a failed overwrite leaves the old file untouched and leaves no .part file", raised and (tmp / "photo.webp").read_bytes() == b"KEEP" and not [p for p in tmp.iterdir() if p.suffix == ".part" or p.name.endswith(".part")], sorted(p.name for p in tmp.iterdir()))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def f2_seed_race():
    tmp = Path(tempfile.mkdtemp(prefix="wp_pl_f2_"))
    try:
        (tmp / "wf.json").write_text(json.dumps({"1": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 5}}}), encoding="utf-8")
        img = tmp / "r.png"; img.write_bytes(png_bytes())
        for fixed in (42, 0):
            prompts = [{"id": "A", "name": "A", "text": "a"}]
            holder = {}

            async def fake(wf, on_img, on_err, on_done, stop_event=None, on_stop=None):
                holder["ns"]["_current_seed"][0] = 999          # while waiting the person opened an older image (seed 999): the UI state changed
                await on_img(str(img)); await on_done()

            ns, statuses = _run_apis_env(tmp, prompts, fake)
            holder["ns"] = ns
            ns["_current_seed"][0] = fixed
            run = _nested("_run_apis", ns)
            asyncio.run(run("seed"))
            entry = ns["_prompt_images"][0][0]
            check(f"F2 the image keeps the seed it was made with ({fixed}), not the one on screen when it arrived (999)", entry["seed"] == fixed, entry)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def f3_chat_height():
    check("F3 one shared clamp exists", hasattr(PL, "llm_height_limits"))
    if not hasattr(PL, "llm_height_limits"):
        return
    for h, mode in ((600, "compact"), (768, "medium"), (900, "wide"), (1080, "wide")):
        lo, hi = PL.llm_height_limits(h, mode)
        check(f"F3 {h} high / {mode}: lo {lo}, hi {hi}: the chat never gets more than the window minus the editor's minimum, nor less than 150", lo == 150 and hi >= lo and (hi == lo or hi <= h - 40 - (48 if mode != "wide" else 0) - PL._EDITOR_MIN_H), (lo, hi))
    src = inspect.getsource(PL.main)
    check("F3 the divider drag and the window resize use the same clamp", src.count("llm_height_limits(") >= 2)


def f4_editor_width():
    check("F4 one shared clamp exists", hasattr(PL, "wide_widths"))
    if not hasattr(PL, "wide_widths"):
        return
    for W in (1600, 1920, 2560):
        for left, mid in ((195, 420), (400, 4000), (-50, -50), (400, 900), (195, 5)):
            l, m = PL.wide_widths(W, left, mid)
            gen = W - l - m - 10
            check(f"F4 window {W}, asked left={left} mid={mid} -> left {l}, editor {m}, generation {gen:.0f}", 140 <= l <= 400 and m >= PL._MID_MIN and gen >= PL._GEN_MIN, (l, m, gen))
    src = inspect.getsource(PL.main)
    check("F4 the editor drag, the list drag and the window resize all apply the clamp", src.count("wide_widths(") >= 3)


def f5_blank_seed():
    class Ctl(types.SimpleNamespace):
        pass

    seed_field = Ctl(value="123", color="AMBER")
    ns = dict(preview_img=Ctl(src=None, visible=True, page=None), preview_ph=Ctl(visible=False), btn_save=Ctl(disabled=False), img_tab_info=Ctl(value="wf  ·  seed 123"), _BLANK=b"x", page=types.SimpleNamespace(update=lambda: None))
    blank = _nested("_show_blank", ns)
    blank()
    check("F5 a prompt with no image shows no image info (the old seed is not presented as its own)", ns["img_tab_info"].value == "" and ns["preview_img"].visible is False)
    check("F5 the generation seed (the one Run uses) is not cleared by merely selecting an empty prompt", seed_field.value == "123")


def f6_unmounted_panel():
    """The real app crashed on start in a narrow window: the Generate panel is not on screen, and asking such a control for .page raises."""
    class Ghost:                                   # a control that is not in the page right now
        visible = True
        src = None
        disabled = False

        @property
        def page(self):
            raise RuntimeError("Image(195) Control must be added to the page first")

    class Ctl:
        pass

    updates = []
    ns = dict(preview_img=Ghost(), preview_ph=Ctl(), btn_save=Ctl(), img_tab_info=Ctl(), _BLANK=b"x", page=types.SimpleNamespace(update=lambda: updates.append(1)))
    blank = _nested("_show_blank", ns)
    try:
        blank()
        ok = True
    except RuntimeError:
        ok = False
    check("F6 _show_blank does not touch .page of a control that is not on screen (start in a narrow window)", ok and updates, updates)
    src = inspect.getsource(PL.main)
    import re
    check("F6 no code in main() asks a control (other than the page itself) for .page", not re.search(r"(?<![\w.])(?!page)\w+\.page", src), re.findall(r"(?<![\w.])(?!page)\w+\.page", src))


def f7_blocks_only():
    """Only REPLY -> PROMPT and PROMPT -> END count; whatever is outside them is dropped."""
    body = "=========== REPLY ===========\nok\n\n=========== PROMPT ===========\nA prompt line.\n\n=========== END ==========="
    for tail in ("\n<|eos|>", "\n<|im_end|>", "\n</s>", "\n</end_of_turn>", "\n<|eos|>\n<|eos|>", "\nsome more text", "\n<|eos|> and then words", "\n\n\n"):
        try:
            reply, prompt = PL._parse_llm_output(body + tail)
            ok = reply == "ok" and prompt == "A prompt line."
        except PL.LLMFormatError:
            ok = False
        check(f"F7 anything after END is dropped: {tail.strip()!r}", ok)
    try:
        reply, prompt = PL._parse_llm_output("Sure, here it is:\n" + body)
        ok = reply == "ok" and prompt == "A prompt line."
    except PL.LLMFormatError:
        ok = False
    check("F7 anything before REPLY is dropped", ok)
    try:
        PL._parse_llm_output("=========== REPLY ===========\nok\n=========== PROMPT ===========\ncut off in the mid")
        ok = False
    except PL.LLMFormatError:
        ok = True
    check("F7 a missing END is still refused (a cut-off answer is not accepted)", ok)


def main():
    for fn in (b1, b2, b3, b4, b5, f1_save_as_collision, f2_seed_race, f3_chat_height, f4_editor_width, f5_blank_seed, f6_unmounted_panel, f7_blocks_only):
        try:
            fn()
        except Exception as ex:                                       # a crash is a failure, not a silent skip
            import traceback
            traceback.print_exc()
            _fails.append(f"{fn.__name__} crashed: {ex}")
            print(f"FAIL  {fn.__name__} crashed: {ex}")
    print("ALL PASS" if not _fails else f"{len(_fails)} FAILED: {_fails}")
    return 0 if not _fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
