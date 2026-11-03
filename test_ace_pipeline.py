"""Read-only audit: lyrics editor text through compile_lyrics, prepare and build_workflow.

No audio is submitted. ComfyUI is not called. Song files are not written.
"""
import inspect
import json
import shutil
import tempfile
from pathlib import Path

from music_studio import comfy_audio as CA
from music_studio import compiler as K
from music_studio import generation as G
from music_studio import i18n
from music_studio import live_insights as LI
from music_studio import lyric_rhythm as RT
from music_studio import quality_presets as QP
from music_studio import section_preview as PV
from music_studio.model import Song, SongSettings

AUDIO = Path(__file__).resolve().parent / "comfy_apis" / "audio"
TAGS = "night pop ballad"


def _forbid(*_a, **_k):
    raise AssertionError("this audit must not submit or execute audio")


CA.submit = _forbid
G.execute = _forbid


def _text_node(wf: dict) -> dict:
    nodes = [n for n in wf.values() if isinstance(n, dict) and n.get("class_type") == CA.TEXT_CLS]
    assert len(nodes) == 1
    return nodes[0]


def _sampler(wf: dict) -> dict:
    nodes = [n for n in wf.values() if isinstance(n, dict) and n.get("class_type") == CA.SAMPLER_CLS]
    assert len(nodes) == 1
    return nodes[0]


def _latent(wf: dict) -> dict:
    nodes = [n for n in wf.values() if isinstance(n, dict) and n.get("class_type") == CA.LATENT_CLS]
    assert len(nodes) == 1
    return nodes[0]


def _unet(wf: dict) -> str:
    names = [str((n.get("inputs") or {}).get("unet_name", "")) for n in wf.values() if isinstance(n, dict) and n.get("class_type") == "UNETLoader"]
    assert len(names) == 1
    return names[0]


def trace(editor: str, workflow: str = "", duration: int = 180, bpm: int = 100):
    comp = K.compile_lyrics(editor)
    song = Song(title="pipeline-audit", lyrics=editor, settings=SongSettings(
        workflow=workflow, duration=duration, bpm=bpm, seed=7, language="auto", style_mode="advanced", style_prompt=TAGS))
    wf, used = G.prepare(AUDIO, song, TAGS, comp.ace)
    node = _text_node(wf)
    assert node["inputs"]["lyrics"] == comp.ace
    assert "Notes about the markup" not in node["inputs"]["lyrics"]
    assert node["inputs"]["tags"] == TAGS
    assert TAGS not in node["inputs"]["lyrics"]
    assert node["inputs"]["bpm"] == bpm and node["inputs"]["duration"] == duration
    assert _latent(wf)["inputs"]["seconds"] == duration
    assert used["bpm"] == bpm and used["duration"] == duration and used["steps"] == song.settings.steps and used["cfg"] == song.settings.cfg
    assert int(_sampler(wf)["inputs"]["steps"]) == int(song.settings.steps)
    assert abs(float(_sampler(wf)["inputs"]["cfg"]) - float(song.settings.cfg)) < 1e-6
    assert node["inputs"]["generate_audio_codes"] is True
    return comp, song, wf, used, node


def test_clean_lyrics_are_sent_unchanged_including_a_second_chorus():
    clean = "[Verse 1]\nฉันยังรอเธอตรงที่เดิม\nริมหน้าต่างที่ฝนเพิ่งหยุด\n\n[Chorus]\nกลับมาได้ไหม\nฉันยังอยู่ตรงนี้\n\n[Verse 2]\nคืนนี้เงียบไป\n\n[Chorus]\nกลับมาได้ไหม\nฉันยังอยู่ตรงนี้"
    comp = K.compile_lyrics(clean)
    assert comp.ace == clean
    _comp, _song, _wf, _used, node = trace(clean)
    assert node["inputs"]["lyrics"] == clean
    assert node["inputs"]["language"] == "th"


def test_layout_only_changes_are_blanks_and_html_not_section_text():
    editor = "\n[Verse 1]\nฉันยังรอเธอตรงที่เดิม\nริมหน้าต่างที่ฝนเพิ่งหยุด\n\n\n[Chorus]\nกลับมาได้ไหม\nฉันยังอยู่ตรงนี้\n[Verse 2]\nคืนนี้เงียบไป\n\n[Chorus - whispered, anthemic, powerful belting]\nกลับมาได้ไหม\n\n[Bridge]\n[Bridge]\nฉันยังอยู่ตรงนี้\n\n[Verse 1]\nฉันยังรอเธอตรงที่เดิม\n\n[Intro — Rhodes piano]\n\n<b>ฉันยังอยู่ตรงนี้</b>\n[Chorus] กลับมาได้ไหม"
    comp = K.compile_lyrics(editor)
    ace = comp.ace
    assert "ฉันยังอยู่ตรงนี้\n[Verse 2]" in ace
    assert "\n\n\n" not in ace
    assert "[Chorus - whispered, anthemic, powerful belting]" in ace
    assert "[Bridge]\n[Bridge]" in ace
    assert ace.count("[Verse 1]") == 2
    assert "[Intro — Rhodes piano]" in ace
    assert "<b>" not in ace and "ฉันยังอยู่ตรงนี้" in ace
    assert "[Chorus] กลับมาได้ไหม" in ace
    assert any(level == "warning" and "Removed tags" in msg for level, _n, msg in comp.warnings)
    assert any("not a documented" in msg for _level, _n, msg in comp.warnings)
    _comp, song, wf, used, node = trace(editor, duration=45, bpm=90)
    assert node["inputs"]["lyrics"] == ace
    assert node["inputs"]["duration"] == 45 and _latent(wf)["inputs"]["seconds"] == 45
    assert used["language"] == "th"
    assert song.settings.workflow == "audio_ace_step_1_5_split.json"
    assert "turbo" in _unet(wf).lower()
    assert _sampler(wf)["inputs"]["steps"] == 8 and _sampler(wf)["inputs"]["cfg"] == 1.0
    assert "workflow_notice" not in used


def test_glued_sections_are_not_given_a_blank_line():
    glued = "[Chorus]\nกลับมาได้ไหม\n[Verse 2]\nคืนนี้เงียบไป"
    assert K.compile_lyrics(glued).ace == glued


def test_section_labels_keep_an_internal_hyphen_and_the_lyrics_stay_whole():
    text = "[Pre-Chorus]\nกลับมาได้ไหม\n\n[Post-Chorus - soft]\nฉันยังอยู่ตรงนี้\n\n[Intro — Rhodes piano]\nริมหน้าต่างที่ฝนเพิ่งหยุด"
    assert K.compile_lyrics(text).ace == text
    assert K.section_label("[Pre-Chorus]") == "Pre-Chorus"
    assert K.section_label("[Post-Chorus - soft]") == "Post-Chorus"
    assert K.section_label("[Intro — Rhodes piano]") == "Intro"
    assert LI.section_name("[Pre-Chorus]") == "Pre-Chorus"
    assert LI.section_name("[Post-Chorus - soft]") == "Post-Chorus"
    assert LI.section_name("[Intro — Rhodes piano]") == "Intro"
    assert LI._label_of("[Pre-Chorus]") == "Pre-Chorus"
    names = RT.section_names("[Intro — Rhodes piano]\nริมหน้าต่างที่ฝนเพิ่งหยุด\n[Pre-Chorus]\nกลับมาได้ไหม\n[Hold]\nฉันยังอยู่ตรงนี้\n")
    assert names[1] == "Intro" and names[2] == "Intro"
    assert names[3] == "Pre-Chorus" and names[4] == "Pre-Chorus" and names[5] == "Pre-Chorus"
    ins = LI.analyze(text)
    assert [s.name for s in ins.sections] == ["Pre-Chorus", "Post-Chorus", "Intro"]
    assert ins.sections[0].tag == "[Pre-Chorus]"
    repeated = "[Pre-Chorus]\nกลับมาได้ไหม\n\n[Chorus]\nฉันยังอยู่ตรงนี้\n\n[Pre-Chorus]\nกลับมาได้ไหม\n"
    ins2 = LI.analyze(repeated)
    health = LI.health(ins2, LI.density(ins2, LI.DensitySettings(bpm=100, timesig="4", duration=180, speed="normal")))
    assert "duplicate_section" not in [h.kind for h in health]
    assert [s.name for s in ins2.sections] == ["Pre-Chorus", "Chorus", "Pre-Chorus"]


def test_every_workflow_keeps_the_lyrics_and_records_its_sampler():
    editor = "[Verse 1]\nฉันยังรอเธอตรงที่เดิม\n\n[Chorus]\nกลับมาได้ไหม\n\n[Chorus]\nฉันยังอยู่ตรงนี้"
    comp = K.compile_lyrics(editor)
    files = CA.scan_workflows(AUDIO)
    assert [p.name for p in files][0] == "audio_ace_step1_5_xl_sft.json"
    assert len(files) == 4
    for path in files:
        base = json.loads(path.read_text(encoding="utf-8"))
        assert CA.validate_mapping(base) == []
        file_text, file_sampler = _text_node(base), _sampler(base)
        assert file_text["inputs"]["generate_audio_codes"] is True
        assert file_text["inputs"]["duration"] == _latent(base)["inputs"]["seconds"]
        clip_id = str(file_text["inputs"]["clip"][0])
        clip = base[clip_id]
        assert clip["class_type"] == "DualCLIPLoader"
        assert clip["inputs"]["type"] == "ace"
        assert "qwen" in clip["inputs"]["clip_name1"] and "qwen" in clip["inputs"]["clip_name2"]
        assert not [n.get("class_type") for n in base.values() if isinstance(n, dict) and "LM" in str(n.get("class_type", ""))]
        _c, _s, wf, used, node = trace(editor, workflow=path.name, duration=180, bpm=100)
        assert node["inputs"]["lyrics"] == comp.ace
        assert _unet(wf) == _unet(base)
        assert node["inputs"]["generate_audio_codes"] is True
        assert used["steps"] == 8 and used["cfg"] == 1.0
        assert int(file_sampler["inputs"]["steps"]) != 8 or QP.family_of_workflow(base) == QP.DISTILLED
        fam = QP.family_of_workflow(base)
        if fam == QP.FULL:
            assert "sft" in _unet(base).lower()
            assert int(file_sampler["inputs"]["steps"]) == 50 and float(file_sampler["inputs"]["cfg"]) == 7.0
            # This song named the file. Its saved 8 / 1 stays; it is not rewritten to the file's 50 / 7.
            assert used["steps"] == 8 and used["cfg"] == 1.0
        else:
            assert "turbo" in _unet(base).lower()
            assert int(file_sampler["inputs"]["steps"]) == 20 and float(file_sampler["inputs"]["cfg"]) == 1.0


TURBO = "audio_ace_step_1_5_split.json"
SFT = "audio_ace_step1_5_xl_sft.json"


def _prepare(workflow="", steps=8, cfg=1.0, duration=180, wf_params=None, folder=None, lyrics="[Verse 1]\nฉันยังรอเธอตรงที่เดิม\n\n[Chorus]\nกลับมาได้ไหม"):
    settings = SongSettings(workflow=workflow, duration=duration, bpm=100, seed=7, language="en", style_mode="advanced",
                            style_prompt=TAGS, steps=steps, cfg=cfg)
    if wf_params is not None:
        settings.wf_params = wf_params
    song = Song(title="pipeline-audit", lyrics=lyrics, settings=settings)
    comp = K.compile_lyrics(lyrics)
    wf, used = G.prepare(folder or AUDIO, song, TAGS, comp.ace)
    assert _text_node(wf)["inputs"]["lyrics"] == comp.ace
    assert int(_sampler(wf)["inputs"]["steps"]) == int(song.settings.steps)
    assert abs(float(_sampler(wf)["inputs"]["cfg"]) - float(song.settings.cfg)) < 1e-6
    assert int(_latent(wf)["inputs"]["seconds"]) == duration
    assert int(_text_node(wf)["inputs"]["duration"]) == duration
    assert used["steps"] == song.settings.steps and used["duration"] == duration
    return song, wf, used, comp


def test_new_song_defaults_to_plain_turbo_not_the_first_file_name():
    files = CA.scan_workflows(AUDIO)
    assert [p.name for p in files][0] == SFT
    assert QP.pick_default_file(files).name == TURBO
    song, wf, used, _comp = _prepare(workflow="", duration=75)
    base = json.loads((AUDIO / TURBO).read_text(encoding="utf-8"))
    assert song.settings.workflow == TURBO
    assert "turbo" in _unet(wf).lower() and _unet(wf) == _unet(base)
    assert int(_sampler(base)["inputs"]["steps"]) == 20 and float(_sampler(base)["inputs"]["cfg"]) == 1.0
    assert song.settings.steps == 8 and song.settings.cfg == 1.0
    assert int(_sampler(wf)["inputs"]["steps"]) == 8 and float(_sampler(wf)["inputs"]["cfg"]) == 1.0
    assert int(_latent(wf)["inputs"]["seconds"]) == 75
    assert "workflow_notice" not in used


def test_saved_workflow_keeps_its_steps_even_when_they_belong_to_the_other_model():
    song, wf, used, _c = _prepare(workflow=SFT, steps=8, cfg=1.0, duration=180)
    assert song.settings.workflow == SFT and song.settings.steps == 8 and song.settings.cfg == 1.0
    assert "sft" in _unet(wf).lower()
    assert int(_sampler(wf)["inputs"]["steps"]) == 8 and float(_sampler(wf)["inputs"]["cfg"]) == 1.0
    assert "workflow_notice" not in used
    custom, wf2, _u, _c2 = _prepare(workflow=SFT, steps=40, cfg=5, duration=90)
    assert custom.settings.steps == 40 and custom.settings.cfg == 5.0
    assert int(_sampler(wf2)["inputs"]["steps"]) == 40 and float(_sampler(wf2)["inputs"]["cfg"]) == 5.0
    assert int(_latent(wf2)["inputs"]["seconds"]) == 90
    assert QP.level_of(QP.FULL, custom.settings.steps, custom.settings.cfg) == "custom"


def test_switching_workflows_loads_the_model_preset_and_restores_a_custom():
    s = SongSettings(workflow=TURBO, steps=8, cfg=1.0, duration=100, seed=7, language="en")
    QP.switch_workflow(s, TURBO, SFT, QP.family_of_file(AUDIO / SFT))
    s.workflow = SFT
    assert (s.steps, s.cfg) == (50, 7.0)
    song = Song(title="pipeline-audit", lyrics="la", settings=s)
    wf, _used = G.prepare(AUDIO, song, TAGS, "la")
    assert int(_sampler(wf)["inputs"]["steps"]) == 50 and float(_sampler(wf)["inputs"]["cfg"]) == 7.0
    assert int(_latent(wf)["inputs"]["seconds"]) == 100
    QP.switch_workflow(s, SFT, TURBO, QP.family_of_file(AUDIO / TURBO))
    s.workflow = TURBO
    assert (s.steps, s.cfg) == (8, 1.0)
    wf, _used = G.prepare(AUDIO, Song(title="pipeline-audit", lyrics="la", settings=s), TAGS, "la")
    assert s.workflow == TURBO and int(_sampler(wf)["inputs"]["steps"]) == 8 and float(_sampler(wf)["inputs"]["cfg"]) == 1.0
    s.steps, s.cfg = 12, 1.0
    assert QP.level_of(QP.DISTILLED, s.steps, s.cfg) == "high"
    QP.switch_workflow(s, TURBO, SFT, QP.FULL)
    assert (s.steps, s.cfg) == (50, 7.0)
    QP.switch_workflow(s, SFT, TURBO, QP.DISTILLED)
    assert (s.steps, s.cfg) == (12, 1.0)
    s.workflow, s.steps, s.cfg = SFT, 40, 5
    QP.switch_workflow(s, SFT, TURBO, QP.DISTILLED)
    assert (s.steps, s.cfg) == (12, 1.0) and s.wf_params[SFT] == {"steps": 40, "cfg": 5.0}
    QP.switch_workflow(s, TURBO, SFT, QP.FULL)
    s.workflow = SFT
    assert (s.steps, s.cfg) == (40, 5.0)
    wf, _used = G.prepare(AUDIO, Song(title="pipeline-audit", lyrics="la", settings=s), TAGS, "la")
    assert int(_sampler(wf)["inputs"]["steps"]) == 40 and float(_sampler(wf)["inputs"]["cfg"]) == 5.0
    assert QP.level_of(QP.FULL, s.steps, s.cfg) == "custom"


def test_a_missing_file_adopts_turbo_and_remembers_the_old_custom():
    song, wf, used, _c = _prepare(workflow="gone.json", steps=40, cfg=5, duration=60)
    assert song.settings.workflow == TURBO
    assert song.settings.wf_params["gone.json"] == {"steps": 40, "cfg": 5.0}
    assert song.settings.steps == 8 and song.settings.cfg == 1.0
    assert int(_sampler(wf)["inputs"]["steps"]) == 8 and int(_latent(wf)["inputs"]["seconds"]) == 60
    assert "workflow_notice" not in used


def test_a_saved_memory_of_the_default_file_is_restored():
    song, wf, _used, _c = _prepare(workflow="", wf_params={TURBO: {"steps": 15, "cfg": 1.5}})
    assert song.settings.workflow == TURBO
    assert song.settings.steps == 15 and song.settings.cfg == 1.5
    assert int(_sampler(wf)["inputs"]["steps"]) == 15 and float(_sampler(wf)["inputs"]["cfg"]) == 1.5


def test_without_turbo_a_full_model_gets_its_standard_and_a_notice():
    with tempfile.TemporaryDirectory() as raw:
        folder = Path(raw)
        shutil.copy(AUDIO / SFT, folder / SFT)
        shutil.copy(AUDIO / "audio_ace_step1_5_xl_sft_titled.json", folder / "aaa_tiled.json")
        song, wf, used, comp = _prepare(workflow="", folder=folder, duration=90)
        assert song.settings.workflow == SFT
        assert "tiled" not in song.settings.workflow
        assert song.settings.steps == 50 and song.settings.cfg == 7.0
        assert int(_sampler(wf)["inputs"]["steps"]) == 50 and float(_sampler(wf)["inputs"]["cfg"]) == 7.0
        assert int(_latent(wf)["inputs"]["seconds"]) == 90
        assert _text_node(wf)["inputs"]["lyrics"] == comp.ace
        notice = used["workflow_notice"]
        assert notice.startswith("ACE-Step 1.5 Turbo was not found")
        assert SFT in notice and "50" in notice and "CFG 7" in notice
        kept, wf2, used2, _c = _prepare(workflow="", steps=12, cfg=1.0, folder=folder)
        assert kept.settings.steps == 12 and kept.settings.cfg == 1.0
        assert int(_sampler(wf2)["inputs"]["steps"]) == 12
        assert "12" in used2["workflow_notice"] and "CFG 1" in used2["workflow_notice"]
        named, wf3, used3, _c = _prepare(workflow=SFT, steps=8, cfg=1.0, folder=folder)
        assert named.settings.steps == 8 and named.settings.cfg == 1.0
        assert int(_sampler(wf3)["inputs"]["steps"]) == 8
        assert "workflow_notice" not in used3


def test_a_graph_without_a_turbo_unet_is_not_the_turbo_default():
    with tempfile.TemporaryDirectory() as raw:
        folder = Path(raw)
        data = json.loads((AUDIO / TURBO).read_text(encoding="utf-8"))
        data = {k: v for k, v in data.items() if not (isinstance(v, dict) and v.get("class_type") == "UNETLoader")}
        (folder / "aaa_no_unet.json").write_text(json.dumps(data), encoding="utf-8")
        shutil.copy(AUDIO / TURBO, folder / "zzz_plain.json")
        song, wf, used, _c = _prepare(workflow="", folder=folder)
        assert song.settings.workflow == "zzz_plain.json"
        assert "turbo" in _unet(wf).lower()
        assert song.settings.steps == 8 and song.settings.cfg == 1.0
        assert "workflow_notice" not in used
        only = folder / "only"
        only.mkdir()
        (only / "bare.json").write_text(json.dumps(data), encoding="utf-8")
        bare, bare_wf, bare_used, _c = _prepare(workflow="", folder=only)
        assert bare.settings.workflow == "bare.json"
        assert QP.family_of_workflow(bare_wf) == QP.DISTILLED
        assert bare.settings.steps == 8 and bare.settings.cfg == 1.0
        assert [n for n in bare_wf.values() if isinstance(n, dict) and n.get("class_type") == "UNETLoader"] == []
        assert "Turbo was not found" in bare_used["workflow_notice"]


def test_plain_turbo_wins_over_an_earlier_tiled_file_name():
    with tempfile.TemporaryDirectory() as raw:
        folder = Path(raw)
        shutil.copy(AUDIO / "audio_ace_step_1_5_split_tiled.json", folder / "aaa_tiled.json")
        shutil.copy(AUDIO / TURBO, folder / "zzz_plain.json")
        scanned = CA.scan_workflows(folder)
        assert scanned[0].name == "aaa_tiled.json"
        assert QP.pick_default_file(scanned).name == "zzz_plain.json"
        song, wf, used, _c = _prepare(workflow="", folder=folder)
        assert song.settings.workflow == "zzz_plain.json"
        assert song.settings.steps == 8 and song.settings.cfg == 1.0
        assert "turbo" in _unet(wf).lower()
        assert "workflow_notice" not in used


def test_no_workflow_file_raises_before_anything_is_sent():
    with tempfile.TemporaryDirectory() as raw:
        song = Song(title="pipeline-audit", lyrics="la", settings=SongSettings(workflow="", seed=7, language="en"))
        try:
            G.prepare(Path(raw), song, TAGS, "la")
        except CA.ComfyError as ex:
            assert "No ACE-Step" in str(ex)
        else:
            raise AssertionError("an empty folder must not build a workflow")


def test_the_ui_paths_use_the_same_resolver():
    preview = inspect.getsource(PV.run_preview)
    prepare = inspect.getsource(G.prepare)
    refresh = (Path(__file__).resolve().parent / "music_studio.py").read_text(encoding="utf-8")
    body = refresh[refresh.index("def refresh_workflows"):refresh.index("def saved_load")]
    assert "resolve_workflow" in preview and "found[0]" not in preview and "check_built" in preview
    assert "resolve_workflow" in prepare and "found[0]" not in prepare and "check_built" in prepare
    assert "resolve_workflow" in body and "names[0]" not in body


def test_the_fallback_notice_has_a_thai_translation():
    notice = QP.fallback_notice(SFT, 50, 7)
    assert not notice.startswith("Line ")
    prev = i18n.lang()
    i18n.set_lang("th")
    try:
        thai = i18n.tr(notice)
        assert thai != notice
        assert "ไม่พบ ACE-Step 1.5 Turbo" in thai and SFT in thai and "50" in thai and "7" in thai
    finally:
        i18n.set_lang(prev)
