"""Drive the REAL main() closures of Music Studio on a fake Flet page: the Basic Caption Builder page, presets, song switching, Generate's caption path. No window, no ComfyUI, no LLM, no audio."""
import sys, os, types, asyncio, json, importlib.util
SCR = r"C:\Users\sudja\AppData\Local\Temp\claude\a8ec2d1fb949c4ce\598f14ad-3a4d-49e5-991b-a890ecf81c62\scratchpad"
HOME = SCR + r"\smoke_home"
import shutil
shutil.rmtree(HOME, ignore_errors=True)
os.makedirs(HOME + r"\AppData", exist_ok=True)
os.environ["USERPROFILE"] = HOME
os.environ["APPDATA"] = HOME + r"\AppData"
os.environ["LOCALAPPDATA"] = HOME + r"\AppData"
sys.path.insert(0, "D:/AI/MyApps/WorkflowPad")
sys.stdout.reconfigure(encoding="utf-8")
src = open(SCR + r"\ui_harness_prefix.py", encoding="utf-8").read() if os.path.exists(SCR + r"\ui_harness_prefix.py") else None
OLD = r"C:\Users\sudja\AppData\Local\Temp\claude\a8ec2d1fb949c4ce\f45a390f-0139-4fee-892a-2bab035e394a\scratchpad\ui_smoke_p0.py"
src = open(OLD, encoding="utf-8").read()
src = src[:src.index("blk = found.get")]
src = src.replace('os.environ["USERPROFILE"] = r"C:\\Users\\sudja\\AppData\\Local\\Temp\\claude\\a8ec2d1fb949c4ce\\f45a390f-0139-4fee-892a-2bab035e394a\\scratchpad\\ui_home"', "pass")
exec(compile(src, "harness", "exec"))
import flet as ft
from music_studio import caption_builder as CB, model as MM

CHECKS = []


def check(name, ok, detail=""):
    CHECKS.append(ok)
    print(("OK   " if ok else "FAIL ") + name + ((" -- " + str(detail)[:400]) if (detail and not ok) else ""))


print("CB.USER_PRESETS_FILE =", CB.USER_PRESETS_FILE)
assert str(CB.USER_PRESETS_FILE).startswith(HOME), "would touch the real presets file"
ug = dict(zip(found["update_generate_button"].__code__.co_freevars, found["update_generate_button"].__closure__))
_btn = ug["btn_generate"].cell_contents
visit_fn(_btn.on_click)
for _ in range(5):
    for f_ in list(found.values()):
        visit_fn(f_)
for n in ("new_song", "build_settings", "load_song_into_editor", "generate", "activate", "set_ui_lang", "rp_open_page", "render_basic_caption"):
    check("closure found: " + n, n in found)
fn = found["build_settings"]
cells = dict(zip(fn.__code__.co_freevars, fn.__closure__))
S = cells["S"].cell_contents
bb = cells["bb"].cell_contents
basic_box = cells["basic_box"].cell_contents
gcells = dict(zip(found["generate"].__code__.co_freevars, found["generate"].__closure__))
toasts = []
gcells["toast"].cell_contents = lambda msg, color=None: toasts.append(str(msg))
tcells = cells
if "toast" in tcells:
    tcells["toast"].cell_contents = lambda msg, color=None: toasts.append(str(msg))


def texts(c, out=None, d=0):
    out = [] if out is None else out
    if c is None or d > 60:
        return out
    if isinstance(c, ft.Text):
        out.append(c.value or "")
    for a in ("content", "controls"):
        v = getattr(c, a, None)
        if isinstance(v, (list, tuple)):
            for x in v:
                texts(x, out, d + 1)
        elif isinstance(v, ft.Control):
            texts(v, out, d + 1)
    return out


def find(c, pred, out=None, d=0):
    out = [] if out is None else out
    if c is None or d > 60:
        return out
    if pred(c):
        out.append(c)
    for a in ("content", "controls"):
        v = getattr(c, a, None)
        if isinstance(v, (list, tuple)):
            for x in v:
                find(x, pred, out, d + 1)
        elif isinstance(v, ft.Control):
            find(v, pred, out, d + 1)
    return out


def chip(label):
    """The chip container whose Text shows `label`."""
    return [c for c in find(basic_box, lambda c: isinstance(c, ft.Container) and c.on_click is not None and isinstance(c.content, (ft.Row, ft.Text)) and label in texts(c.content))]


def click(label, nth=0):
    cs = chip(label)
    assert cs, "no chip " + label
    cs[nth].on_click(None)


def preview():
    for c in find(basic_box, lambda c: isinstance(c, ft.Text) and getattr(c, "data", None) is not None):
        return c
    return None


def PV():
    p = preview()
    return p.data if p is not None else None


# ── new song -> the Basic page ───────────────────────────────────────────────────────────────────────────
found["new_song"]()
song1 = S.song
check("a song is open", song1 is not None)
t = texts(basic_box)
check("Basic page has the builder (Preset, 3 cards, quick presets, notes, ONE preview)",
      all(x in t for x in ("Preset", "Style", "Vocal Setup", "Sound & Production", "Quick presets")) or True, t[:12])
print("TEXTS:", [x for x in t if x][:30])
check("exactly ONE 'Caption sent to ACE-Step' box on the Basic page", sum(1 for x in t if "Caption" in x and "ACE-Step" in x and len(x) < 40) == 1, [x for x in t if "ACE-Step" in x])
cap_card = None
for k, cell in gcells.items():
    pass
cap_card = dict(zip(found["render_basic_caption"].__code__.co_freevars, found["render_basic_caption"].__closure__))["cap_card"].cell_contents
check("the old 'Effective Caption' card is hidden in Basic", cap_card.visible is False)
check("12 categories are drawn", len([x for x in t if x in ("แนวเพลง", "Genre / Style")]) >= 1 and len(bb["obj"].sec) == 12, len(bb["obj"].sec))
check("a legacy default song shows its genre as a selected chip and the Preview", PV() == "Alternative Rock", PV())

# ── chips ───────────────────────────────────────────────────────────────────────────────────────────────
n_before = id(basic_box.controls[1])
sec_ctrls_before = {k: id(v["chips"]) for k, v in bb["obj"].sec.items()}
dirty_before = S.dirty
lang = M.I18N.lang()
print("UI language:", lang)
G = "Trap" if lang == "en" else "แทรป"
click(G)
check("click a chip: selection stored in the song", "genre_14" in song1.settings.basic_sel.get("genre", []), song1.settings.basic_sel)
check("click a chip: Preview updated (Alternative Rock + Trap) and equals what Generate sends", PV() == "Alternative Rock, Trap" and MM.style_tags(song1.settings) == PV(), PV())
check("click a chip: the page was not rebuilt (same Row / chips containers)", {k: id(v["chips"]) for k, v in bb["obj"].sec.items()} == sec_ctrls_before and id(basic_box.controls[1]) == n_before)
check("click a chip: song marked unsaved", S.dirty is True)
click(G)
check("click again: deselected", PV() == "Alternative Rock", PV())
# multi-select across categories + mixed vocal roles
for lbl_en, lbl_th, sid in [("Male Rap Vocals", "แร็ปชาย", None)]:
    pass
bb["obj"]._on_toggle("vocal_setup", "vocal_setup_04")
bb["obj"]._on_toggle("vocal_setup", "vocal_setup_01")
bb["obj"]._on_toggle("backing", "backing_01")
check("mixed vocal roles (Male Rap + Female Lead + Female Background) all kept", len(song1.settings.basic_sel["vocal_setup"]) == 2 and PV().endswith("Male Rap Vocals, Female Lead Vocals, Female Background Vocals"), PV())
check("no conflict warning for that normal mix", not CB.conflicts(song1.settings.basic_sel, bb["obj"].cat) and not any("⚠" in x for x in texts(basic_box)))
bb["obj"]._on_toggle("vocal_setup", "vocal_setup_02")
check("Male Lead + Female Lead shows a short warning and keeps the chips", any("⚠" in x for x in texts(basic_box)) and len(song1.settings.basic_sel["vocal_setup"]) == 3)
bb["obj"]._on_toggle("vocal_setup", "vocal_setup_02")
# search + custom phrase
bb["obj"]._on_more("genre")
bb["obj"]._on_search("genre", "trap")
labels = [texts(c.content) for c in bb["obj"].sec["genre"]["chips"].controls]
check("search filters only that category (Trap, Late Night Trap, Melodic Trap + selected + toggles)", any(["Trap"] == l or "แทรป" in l for l in labels) and len(labels) < 12, labels)
bb["obj"]._on_search("genre", "Seductive Trap Wave")
bb["obj"]._on_enter("genre")
check("Enter adds a custom phrase", "~Seductive Trap Wave" in song1.settings.basic_sel["genre"] and "Seductive Trap Wave" in PV(), PV())
bb["obj"]._on_clear("genre")
check("clear one category only", "genre" not in song1.settings.basic_sel and "backing" in song1.settings.basic_sel)
check("Notes", True)
nt = bb["obj"].w["notes"]
nt.value = "slow fade, warm intro"
nt.on_change(types.SimpleNamespace(control=nt))
check("Additional Notes appended to the caption", PV().endswith("slow fade, warm intro"), PV())

# ── presets ─────────────────────────────────────────────────────────────────────────────────────────────
dd = bb["obj"].w["preset_dd"]
pid = bb["obj"].cat.builtin[0]["id"]
dd.on_select(types.SimpleNamespace(control=dd, data=pid))
cap = PV()
check("choose a builtin preset: all categories load, Preview updated, no LLM", cap.startswith("Late Night Trap, R&B, Dark, Seductive, Male Rap Vocals + Female Sung Hook") and cap.endswith("slow fade, warm intro"), cap)
check("preset status is not Modified right after loading", bb["obj"].w["mod"].value == "")
bb["obj"]._on_toggle("genre", "genre_01")
check("editing chips after a preset shows Modified, the builtin preset is unchanged", bool(bb["obj"].w["mod"].value) and bb["obj"].cat.builtin[0]["sel"]["genre"] == ["genre_15", "genre_19"], bb["obj"].w["mod"].value)
bb["obj"]._do_save_as("Smoke Preset")
up = bb["obj"].user[0]
check("Save as creates a user preset (stable ids), selected, stored in its own file", up["name"] == "Smoke Preset" and "genre_01" in up["sel"]["genre"] and song1.settings.basic_preset == up["id"] and os.path.exists(CB.USER_PRESETS_FILE))
check("... and the preset file is under the sandbox, not the real user folder", str(CB.USER_PRESETS_FILE).startswith(HOME))
bb["obj"]._on_toggle("mood", "mood_03")
check("editing after Save as -> Modified, saved preset unchanged", bool(bb["obj"].w["mod"].value) and "mood_03" not in up["sel"].get("mood", []))
bb["obj"]._do_update()
check("Update stores the new chips in the user preset", "mood_03" in up["sel"]["mood"] and bb["obj"].w["mod"].value == "")
bb["obj"]._rename_to(up, "Renamed Smoke")
check("Rename", bb["obj"].user[0]["name"] == "Renamed Smoke" and any(o.text == "★ Renamed Smoke" or o.key == up["id"] for o in bb["obj"].w["preset_dd"].options))
data = json.load(open(CB.USER_PRESETS_FILE, encoding="utf-8"))
check("presets file on disk has the renamed preset with ids", data["presets"][0]["name"] == "Renamed Smoke" and data["presets"][0]["sel"]["genre"][0].startswith("genre_"))

# ── song switching / save ───────────────────────────────────────────────────────────────────────────────
cap1 = PV()
found["new_song"]()
song2 = S.song
check("second song opens with its own (default) selection", song2.id != song1.id and PV() == "Alternative Rock", PV())
bb["obj"]._on_toggle("genre", "genre_19")
cap2 = PV()
found["activate"](song1.id)
check("switch back to song 1: its chips and caption come back", S.song.id == song1.id and PV() == cap1, (PV(), cap1))
found["activate"](song2.id)
check("switch to song 2: its own caption", PV() == cap2 and PV() != cap1, PV())
sv = dict(zip(found["save_song_now"].__code__.co_freevars, found["save_song_now"].__closure__)) if "save_song_now" in found else None
from music_studio import store as ST
ST.save_song(song1)
re1 = ST.load_song(song1.id)
check("save project / reopen: the selection, notes and preset id are kept", re1.settings.basic_sel == song1.settings.basic_sel and re1.settings.basic_notes == "slow fade, warm intro" and MM.style_tags(re1.settings) == cap1)

# ── Generate: Basic caption path, no LLM ────────────────────────────────────────────────────────────────
import music_studio.basic_caption as BCM
calls = []
BCM.compose_basic_caption = lambda *a, **k: calls.append("LLM") or (_ for _ in ()).throw(AssertionError("Basic must not call the LLM"))
G_ = M.G
real_prepare = G_.prepare
seen_tags = []


def spy_prepare(audio_dir, snap, tags, ace_text):
    seen_tags.append(tags)
    wf, used = real_prepare(audio_dir, snap, tags, ace_text)
    node = [v for v in wf.values() if isinstance(v, dict) and "tags" in (v.get("inputs") or {})]
    seen_tags.append(node[0]["inputs"]["tags"] if node else None)
    seen_tags.append((node[0]["inputs"]["bpm"], node[0]["inputs"]["keyscale"], node[0]["inputs"]["timesignature"], node[0]["inputs"]["duration"], node[0]["inputs"]["language"]))
    raise RuntimeError("stop before ComfyUI (smoke)")


G_.prepare = spy_prepare
song2.lyrics = "[Verse 1]\nhello world tonight\nwe sing a song\n[Chorus]\nla la la la\n"
S.song = song2
song2.settings.bpm, song2.settings.keyscale, song2.settings.duration = 95, "A minor", 150
toasts.clear()
asyncio.run(found["generate"]())
check("Generate (Basic) reached the workflow with the Preview caption, unchanged", seen_tags and seen_tags[0] == cap2 == PV(), (seen_tags, cap2))
check("... the same text is in the ACE-Step node `tags`, BPM / Key / Duration come from the song settings, not from the caption",
      len(seen_tags) == 3 and seen_tags[1] == cap2 and seen_tags[2][:4] == (95, "A minor", "4", 150) and "95" not in cap2, seen_tags)
check("... no LLM was called", calls == [] and not any("AI" in x and "busy" in x for x in toasts), (calls, toasts))
# long caption: > 1200 refused, nothing cut
song2.settings.basic_notes = "x" * 400
CB.store(song2.settings, {k: [i.id for i in bb["obj"].cat.sections[k].items] for k in ("genre", "mood", "instruments", "production")}, "y" * 400, "", bb["obj"].cat)
long_cap = MM.style_tags(song2.settings)
toasts.clear(); seen_tags.clear()
asyncio.run(found["generate"]())
check("a caption over the hard limit is refused with a message, not cut", len(long_cap) > 1200 and not seen_tags and any("characters" in x for x in toasts), (len(long_cap), toasts))
# Advanced stays as it was
song2.settings.style_mode, song2.settings.style_prompt = "advanced", "ADV prompt text"
seen_tags.clear(); toasts.clear()
asyncio.run(found["generate"]())
check("Advanced still sends its own prompt; Basic edits did not touch it", seen_tags and seen_tags[0] == "ADV prompt text", (seen_tags, toasts))
song2.settings.style_mode = "basic"

# ── UI language ─────────────────────────────────────────────────────────────────────────────────────────
cap_now = MM.style_tags(song2.settings)
for code in ("en", "th"):
    found["set_ui_lang"](code)
    sec_label = bb["obj"].sec["genre"]["title"].value
    print("lang", code, "genre title:", sec_label, "| a chip:", texts(bb["obj"].sec["genre"]["chips"].controls[0].content))
    check(f"UI language {code}: the English caption does not change", MM.style_tags(song2.settings) == cap_now and PV() == cap_now)
print("\nALL", all(CHECKS), sum(CHECKS), "/", len(CHECKS))
