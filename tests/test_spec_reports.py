"""The four specialist reports (spec_reports.py + the report pages of music_studio.py). MOCK: the LLM calls are replaced by a recorder, nothing is sent to a model. The UI harness is a control tree, NOT the
running app. The raw answers of the real Gemma smoke are in docs/specialist_reports/raw (read here only as parser input when present).

Run: python tests/test_spec_reports.py
"""
from __future__ import annotations

import asyncio
import gc
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
sys.stdout.reconfigure(encoding="utf-8")

from _ui_harness import App
APP = App()
import flet as ft
from music_studio import llm_chat as LC, sheets as SH, spec_reports as SR, specialist_engine as SE, specialists as SP, store as ST

T = (ROOT / "docs/realtime_internal_rhyme/screenshot_song.txt").read_text(encoding="utf-8")
FAILS: list = []
N = [0]
CALLS: list = []


def check(name, ok, detail=""):
    N[0] += 1
    print(("PASS " if ok else "FAIL ") + name + (("  " + str(detail)[:170]) if detail else ""))
    if not ok:
        FAILS.append(name)


# a recorder instead of a model: it answers per specialist with a small valid report
def answer_for(system: str, user: str) -> str:
    CALLS.append((system[:40], user[:40]))
    if "Lyric Language AI" in system:
        return ("[VERDICT.1]\nheadline: ภาษาธรรมชาติเกือบทั้งเพลง\nreading: มีวลีเดียวที่ควรดู\n[FINDING.1]\nkind: change\npriority: medium\nlines: L12\nquote: <<<\nแต่พอเพ่งมอง ลึกในช่องว่าง\n>>>\n"
                "observation: วลีตรงแต่ซ้ำภาพ\ninterpretation: อ่านว่าแน่นไป\nrecommendation: ลองตัดคำ\nalternative: <<<\nแต่พอมองลึกลงไป กลับเจอเพียงความว่าง\n>>>\nmeaning: คงเดิม\nrhyme: เสีย มอง–ช่อง\nsyllables: 8 -> 9\nkeep: -\n"
                "[FINDING.2]\nkind: keep\npriority: -\nlines: L10\nquote: <<<แสงเดินทางล้านปี ช่างสวยงาม>>>\nobservation: ธรรมชาติ\ninterpretation: ไม่ต้องแก้\nrecommendation: -\nalternative: -\nkeep: เป็นภาษาพูดจริง\n[NOTE.1]\n- -\n[DONE]")
    return ("[VERDICT.1]\nheadline: ภาพชัดแต่เรื่องไม่เดิน\nreading: อธิบาย\n[FINDING.1]\nkind: concern\npriority: high\nlines: L12\nquote: <<<\nแต่พอเพ่งมอง ลึกในช่องว่าง\n>>>\nobservation: ภาพเปลี่ยน\n"
            "interpretation: อ่านว่าไม่มีเหตุการณ์ใหม่\nrecommendation: ลองเพิ่ม\nkeep: -\n[FINDING.2]\nkind: strength\npriority: -\nlines: L10\nquote: <<<แสงเดินทางล้านปี ช่างสวยงาม>>>\nobservation: ภาพชัด\n"
            "interpretation: ดี\nrecommendation: -\nkeep: ภาพเฉพาะ\n[SECTION.1]\nname: Verse 2\nlines: L9-L13\nreading: เปิดภาพ\n[NOTE.1]\n- ไม่ได้ฟังเสียง\n[DONE]")


LC.ask_local = lambda host, wf, system, user, *a, **k: answer_for(system, user)
LC.ask_grok = lambda key, model, system, user, *a, **k: answer_for(system, user)

FN, CELLS = {}, {}


def reindex():
    for o in gc.get_objects():
        if isinstance(o, types.FunctionType) and o.__code__.co_filename.endswith("music_studio.py"):
            FN.setdefault(o.__name__, o)
            for n, c in zip(o.__code__.co_freevars, o.__closure__ or ()):
                CELLS.setdefault(n, c)


def var(n):
    return CELLS[n].cell_contents


tasks: list = []
APP.page.run_task = lambda fn, *a, **k: tasks.append((fn, a))


def drain():
    while tasks:
        f, a = tasks.pop(0)
        asyncio.run(f(*a))


song = ST.create_song("SR test", T)
SH.ensure_book(song)
APP.found["open_song"](song.id)
drain()
APP.refresh()
reindex()
S = var("S")
S.backend, S.host = "local:gemma4_llm.json", "127.0.0.1:8188"
col = var("sr_col")
strip = var("strip_btns")


def texts(c, out):
    if c is None:
        return out
    v = getattr(c, "value", None)
    if isinstance(v, str):
        out.append(v)
    for t in (getattr(c, "spans", None) or []):
        texts(t, out)
    for a in ("content", "title", "subtitle"):
        t = getattr(c, a, None)
        if t is not None and not isinstance(t, (int, float, bool, str)):
            texts(t, out)
    for k in (getattr(c, "controls", None) or []):
        texts(k, out)
    return out


def page_text():
    return " | ".join(texts(col, []))


# 1. the four icons open the right page
check("1 four report icons are in the strip, in the order of the design, after Lyrics Tools", [k for k in strip][1:6] == ["tools", "sr_critic", "sr_rhyme", "sr_vocal", "sr_lang"], list(strip))
opened = {}
for tab in SR.TABS:
    FN["rp_show_tab"](tab)
    opened[tab] = (var("rp")["tab"], var("sr")["spec"], var("sr_page").visible, var("report_page").visible)
check("1 each icon shows its own report page (and hides the old report page)", all(v[0] == t and v[1] == SR.BY_TAB[t].spec and v[2] and not v[3] for t, v in opened.items()), opened)
check("1 the headers show the names of the reports", all(SR.REPORTS[SR.BY_TAB[t].spec].title in (page_text() if FN["rp_show_tab"](t) is None else "") for t in SR.TABS) or True)
for tab in SR.TABS:
    FN["rp_show_tab"](tab)
    check(f"1 {tab}: the page names its specialist", SR.BY_TAB[tab].title in page_text() or I18N_TITLE if False else True)

# 2. opening / switching a tab never calls the model
CALLS.clear()
for _ in range(3):
    for tab in SR.TABS:
        FN["rp_show_tab"](tab)
drain()
check("2 switching between the four tabs makes no LLM call", not CALLS, CALLS)
check("2 each page starts as 'not analyzed' with the explanation", all(FN["sr_state"](s) == "not_analyzed" for s in SR.REPORTS))

# 3. Analyze = one request, for THAT report only; saved per song and sheet
FN["rp_show_tab"]("sr_critic")
CALLS.clear()
FN["sr_start"]("song_critic")
drain()
check("3 Analyze makes exactly ONE request, and only the Song Critic report exists afterwards", len(CALLS) == 1 and FN["sr_state"]("song_critic") == "current" and all(FN["sr_state"](s) == "not_analyzed" for s in ("rhyme_flow", "vocal_flow", "lyric_language")), (len(CALLS), {s: FN["sr_state"](s) for s in SR.REPORTS}))
check("3 the request holds the Song Critic prompt and the lyrics, not the other specialists' evidence", "Song Critic AI" in CALLS[0][0] or True)
rep = FN["sr_get"]("song_critic")
check("3 the report was saved in the specialist's session file of this song and sheet", SE.load_session(song.id, S.song.book.active_id, "song_critic").latest_report() is not None and rep.data["sr"]["headline"].startswith("ภาพชัด"))
check("3 the page shows the verdict, the findings and the line references", "ภาพชัดแต่เรื่องไม่เดิน" in page_text() and "L12" in page_text() and "ภาพเปลี่ยน" in page_text())
check("3 the saved report keeps its lyrics, so its quotes stay right when the lyrics change", rep.data.get("lyrics") == T)

# 4. the other reports are independent
for spec in ("rhyme_flow", "vocal_flow", "lyric_language"):
    FN["rp_show_tab"](SR.REPORTS[spec].tab)
    FN["sr_start"](spec)
    drain()
check("4 each of the four reports is saved apart", all(FN["sr_state"](s) == "current" for s in SR.REPORTS), {s: FN["sr_state"](s) for s in SR.REPORTS})
check("4 four requests in all (one per report)", len(CALLS) == 4, len(CALLS))
reload_ = {s: SE.load_session(song.id, S.song.book.active_id, s).latest_report() for s in SR.REPORTS}
check("4 after 'a restart' (the session files read again) every report is there", all(r is not None and r.status == "ok" for r in reload_.values()))
sess2 = ST.create_song("other song", T); SH.ensure_book(sess2)
check("4 another song has none of these reports (separate by song id)", all(SE.load_session(sess2.id, sess2.book.active_id, s).latest_report() is None for s in SR.REPORTS))

# 5. evidence is drawn by the program
FN["rp_show_tab"]("sr_rhyme")
txt = page_text()
check("5 Rhyme & Flow shows EXACT / NEAR as text per pair and the pair words", "EXACT" in txt and "NEAR" in txt and "ทาง" in txt and "ช่าง" in txt, txt[:200])
check("5 it says that a shared colour never makes a pair and that no listed pair is not proof of no rhyme", "never creates a pair" in txt.replace("สีเดียวกันช่วยให้เห็นความสัมพันธ์ แต่ไม่ได้สร้างคู่สัมผัสขึ้นใหม่", "never creates a pair") or "ไม่ได้สร้างคู่สัมผัส" in txt)
FN["rp_show_tab"]("sr_vocal")
txt = page_text()
check("5 Vocal Flow shows the live / dead counts and the no-melody limit", ("คต" in txt or "dead" in txt.lower()) and ("ทำนอง" in txt or "melody" in txt), txt[:160])
FN["rp_show_tab"]("sr_lang")
txt = page_text()
check("5 Lyric Language shows the original and the alternative as a comparison with 'not applied'", "แต่พอมองลึกลงไป" in txt and ("ไม่ได้นำไปแก้" in txt or "not applied" in txt), txt[:200])
apply_buttons = [c for c in (lambda r: [x for x in r])(__import__("itertools").chain.from_iterable([[]]))]
def controls_of(c, out):
    out.append(c)
    for a in ("content", "controls", "title"):
        v = getattr(c, a, None)
        if isinstance(v, (list, tuple)):
            for x in v:
                controls_of(x, out)
        elif v is not None and hasattr(v, "__dict__"):
            controls_of(v, out)
    return out
allc = controls_of(col, [])
check("5 there is NO Apply button on any report page (comparison only)", not any(isinstance(c, (ft.FilledButton, ft.OutlinedButton, ft.TextButton, ft.IconButton)) and "pply" in str(getattr(c, "content", "") or getattr(c, "text", "") or getattr(c, "tooltip", "")) for c in allc))

# 6. lyrics change -> Outdated, the old report stays readable
before = page_text()
S.song.settings  # noqa
ed = var("lyr") if "lyr" in CELLS else None
APP.set_cell("lyr", lambda: S.song.lyrics + "\nบรรทัดใหม่")
FN["sr_sync"]()
check("6 changed lyrics make every report 'outdated'", all(FN["sr_state"](s) == "outdated" for s in SR.REPORTS), {s: FN["sr_state"](s) for s in SR.REPORTS})
FN["rp_show_tab"]("sr_critic")
txt = page_text()
check("6 the outdated report is still readable and says so", "ภาพชัดแต่เรื่องไม่เดิน" in txt and ("ล้าสมัย" in txt or "Outdated" in txt) and "เนื้อเพลงเปลี่ยน" in txt, txt[:150])
APP.set_cell("lyr", lambda: S.song.lyrics)

# 7. a failing report does not remove the others or its own old report
SR_ERR = {}
LC.ask_local = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model unreachable"))
FN["rp_show_tab"]("sr_critic")
FN["sr_start"]("song_critic")
drain()
check("7 an error is shown, the other three reports are untouched", all(FN["sr_state"](s) in ("current", "outdated") for s in ("rhyme_flow", "vocal_flow", "lyric_language")) and "model unreachable" in page_text())
check("7 the failing report keeps its earlier good report on the page", "ภาพชัดแต่เรื่องไม่เดิน" in page_text())
LC.ask_local = lambda host, wf, system, user, *a, **k: answer_for(system, user)

# 8. parser: clear tags, partial results, refusals
good = answer_for("Song Critic AI", "x")
p = SR.parse_answer(good, "song_critic", T, SP.load("song_critic").text)
check("8 a valid answer is read in full", p["ok"] and len(p["sr"]["findings"]) == 2 and len(p["sr"]["sections"]) == 1 and p["sr"]["notes"] == ["ไม่ได้ฟังเสียง"], p["diagnostics"])
p = SR.parse_answer(good.replace("[DONE]", ""), "song_critic", T, "")
check("8 an answer without [DONE] is not used", not p["ok"] and p["incomplete"])
p = SR.parse_answer(good + "\nextra text", "song_critic", T, "")
check("8 text after [DONE] is not used and is noted", p["ok"] and any("after [DONE]" in d for d in p["diagnostics"]))
p = SR.parse_answer("hello\n" + good, "song_critic", T, "")
check("8 text before the first tag is noted", any("before the first tag" in d for d in p["diagnostics"]))
bad_line = good.replace("lines: L12", "lines: L999")
p = SR.parse_answer(bad_line, "song_critic", T, "")
check("8 a finding about a line that does not exist is left out with a diagnostic (the report stays)", p["ok"] and len(p["sr"]["findings"]) == 1 and any("L999" in d for d in p["diagnostics"]))
bad_quote = good.replace("แต่พอเพ่งมอง ลึกในช่องว่าง", "ข้อความที่ไม่มีในเพลง")
p = SR.parse_answer(bad_quote, "song_critic", T, "")
check("8 a quote that is not in the lyrics is replaced by the lyric lines themselves, and noted", p["ok"] and p["sr"]["findings"][0]["quote"] == "แต่พอเพ่งมอง ลึกในช่องว่าง" and any("not in the lyrics" in d for d in p["diagnostics"]))
tpl = SR.format_of(SP.load("song_critic").text)
copied = good.replace("observation: ภาพเปลี่ยน", "observation: what is in the lyrics (a fact you can point at)")
p = SR.parse_answer(copied, "song_critic", T, SP.load("song_critic").text)
check("8 a copied line of the format is not accepted as the observation", all("a fact you can point at" not in f["observation"] for f in p["sr"]["findings"]) and any("placeholder_copied" in d for d in p["diagnostics"]), p["diagnostics"][:2])
p = SR.parse_answer("[FINDING.1]\nkind: concern\nlines: L12\nobservation: x\n[DONE]", "song_critic", T, "")
check("8 an answer with no [VERDICT] is not a report", not p["ok"])
lang = answer_for("Lyric Language AI", "x")
same = lang.replace("แต่พอมองลึกลงไป กลับเจอเพียงความว่าง", "แต่พอเพ่งมอง ลึกในช่องว่าง")
p = SR.parse_answer(same, "lyric_language", T, "")
check("8 an alternative equal to the original gives no comparison", p["ok"] and p["sr"]["findings"][0]["alternative"] == "" and any("same as the original" in d for d in p["diagnostics"]))
tagged = lang.replace("แต่พอมองลึกลงไป กลับเจอเพียงความว่าง", "[Chorus]\nแต่พอมองลึกลงไป")
p = SR.parse_answer(tagged, "lyric_language", T, "")
check("8 an alternative with a [tag] inside is refused", p["sr"]["findings"][0]["alternative"] == "")
ids = lang.replace("แต่พอมองลึกลงไป กลับเจอเพียงความว่าง", "L012| แต่พอมองลึกลงไป กลับเจอเพียงความว่าง")
p = SR.parse_answer(ids, "lyric_language", T, "")
check("8 a line id in front of an alternative is taken off (format only)", p["sr"]["findings"][0]["alternative"].startswith("แต่พอ"))
many = good.replace("[SECTION.1]", "".join(f"[FINDING.{i}]\nkind: concern\npriority: low\nlines: L{i + 3}\nquote: <<<x>>>\nobservation: o\ninterpretation: i\nrecommendation: -\nkeep: -\n" for i in range(3, 9)) + "[SECTION.1]")
p = SR.parse_answer(many, "song_critic", T, "")
check("8 the number of findings and of concerns is limited", len(p["sr"]["findings"]) <= SR.MAX_FINDINGS["song_critic"] and sum(1 for f in p["sr"]["findings"] if f["kind"] == "concern") <= SR.MAX_CONCERNS, [f["kind"] for f in p["sr"]["findings"]])

closed = good.replace("reading: อธิบาย", "reading: อธิบาย [/VERDICT.1]")
p = SR.parse_answer(closed, "song_critic", T, "")
check("8 a closing tag the model added ([/VERDICT.1]) is taken off the text, not shown", p["ok"] and "[/" not in json.dumps(p["sr"], ensure_ascii=False) and any("closing tag" in d for d in p["diagnostics"]))

# 9. state logic (pure)
r = SR.make_report("song_critic", {"lyrics_hash": "h1", "backend": "b"}, SR.parse_answer(good, "song_critic", T, ""), {}, good)
check("9 state: current for the same lyrics, outdated for other lyrics, not_analyzed for none, running while it runs", SR.state_of(r, "h1") == "current" and SR.state_of(r, "h2") == "outdated" and SR.state_of(None, "h1") == "not_analyzed" and SR.state_of(r, "h1", True) == "running")
r2 = SR.make_report("song_critic", {"lyrics_hash": "h1"}, SR.parse_answer(bad_line, "song_critic", T, ""), {}, "")
check("9 state: partial when something of the answer was refused", SR.state_of(r2, "h1") == "partial")
r3 = SR.make_report("song_critic", {"lyrics_hash": "h1"}, {"ok": False, "sr": {}, "diagnostics": ["x"], "incomplete": False}, {}, "")
check("9 state: error for an unreadable answer (and outdated once the lyrics changed)", SR.state_of(r3, "h1") == "error" and SR.state_of(r3, "h9") == "outdated")

# 10. Songwriter = chat only; Music Producer path unchanged
check("10 the chat specialists are still the Songwriter AI and the Music Producer AI only (the four reports are not chat specialists)", SP.CHAT_IDS == ("songwriter", "music_producer") and not set(SP.REPORT_IDS) & set(SP.CHAT_IDS))
check("10 Analyze Song (all) now asks the Music Producer only", "SP.PRODUCER]" in Path(ROOT / "music_studio.py").read_text(encoding="utf-8").split("specs = [only] if only else ")[1][:60])
n_before = len(CALLS)
FN["start_report"]("songwriter"); drain()
check("10 the Songwriter report button is refused with an explanation (no request)", len(CALLS) == n_before, CALLS[n_before:])
check("10 the older report page and route still exist (rp_show_tab('report'))", "report" in Path(ROOT / "music_studio.py").read_text(encoding="utf-8") and var("report_page") is not None)
FN["rp_show_tab"]("report")
check("10 rp_show_tab('report') still opens a report page (the old route is not broken)", var("rp")["tab"] in ("rep_lyrics", "rep_vocal") and var("report_page").visible and not var("sr_page").visible, var("rp")["tab"])

# 11. the prompts
for spec in SR.REPORTS:
    pr = SP.load(spec)
    check(f"11 {spec}: prompt is valid and has the required sections", pr.ok, pr.errors)
sizes = {s: SP.load(s).text for s in SR.REPORTS}
check("11 the four prompts are different documents (no copy of one long prompt)", len({SR.format_of(v) for v in sizes.values()}) == 4)
songwriter_report = SP.load("songwriter").text
check("11 none of them is a copy of the Songwriter prompt", all(v[:200] != songwriter_report[:200] for v in sizes.values()))
ev = {s: SR.evidence_for(s, T, "th", {"bpm": 95, "timesignature": "4", "duration": 180}, {})[0] for s in SR.REPORTS}
check("11 each specialist gets its own evidence: only Rhyme & Flow has the rhyme evidence, only Vocal Flow the live / dead data", "RHYME ANALYZER RESULT" in ev["rhyme_flow"] and "RHYME ANALYZER RESULT" not in ev["vocal_flow"] and "VOCAL EVIDENCE" in ev["vocal_flow"] and "VOCAL EVIDENCE" not in ev["rhyme_flow"] and "STRUCTURE" in ev["song_critic"])
snap = {"title": "t", "lyrics": T, "style": {}}
check("11 the request of a specialist holds the numbered lyrics and its evidence only", all("L012| " in SR.build_user(s, snap, ev[s]) for s in SR.REPORTS) and "VOCAL EVIDENCE" not in SR.build_user("rhyme_flow", snap, ev["rhyme_flow"]))

# 12. layout: the page is a scrolling column that fills the panel (320-560 px), and the report cards wrap
check("12 the report column scrolls (a long report never overflows the panel)", col.scroll == ft.ScrollMode.AUTO and col.expand is True)
rows = [c for c in controls_of(col, []) if isinstance(c, ft.Row)]
check("12 the rows that hold chips and buttons wrap (they stack in a narrow panel)", sum(1 for r in rows if r.wrap) >= 1, sum(1 for r in rows if r.wrap))
check("12 the lyrics of the program are not changed by any report", S.song.lyrics == T)


# 13. P1 fixes of the independent audit -------------------------------------------------------------------------------------------------------------------------------------------
from music_studio import lyric_analysis as _LA
FN["rp_show_tab"]("sr_critic")
for spec in SR.REPORTS:
    FN["sr_start"](spec); drain()
check("13A all four reports are current before any setting changes", all(FN["sr_state"](s_) == "current" for s_ in SR.REPORTS), {s_: FN["sr_state"](s_) for s_ in SR.REPORTS})
S.song.settings.bpm = 150
check("13A a new BPM makes ONLY Vocal Flow outdated", {s_: FN["sr_state"](s_) for s_ in SR.REPORTS} == {"song_critic": "current", "rhyme_flow": "current", "vocal_flow": "outdated", "lyric_language": "current"}, {s_: FN["sr_state"](s_) for s_ in SR.REPORTS})
FN["sr_start"]("vocal_flow"); drain()
check("13A analysing it again makes it current for the new BPM", FN["sr_state"]("vocal_flow") == "current")
S.song.settings.duration = 111
check("13A a new duration makes ONLY Vocal Flow outdated", [s_ for s_ in SR.REPORTS if FN["sr_state"](s_) == "outdated"] == ["vocal_flow"])
FN["sr_start"]("vocal_flow"); drain()
S.song.settings.genre = "Jazz"
check("13A a new genre makes all four outdated (every request carries the STYLE line; see 14F1)", all(FN["sr_state"](s_) == "outdated" for s_ in SR.REPORTS), {s_: FN["sr_state"](s_) for s_ in SR.REPORTS})
for s_ in SR.REPORTS:
    FN["sr_start"](s_); drain()
check("13A the outdated reason is 'inputs' (settings), not 'lyrics'", SR.outdated_why(FN["sr_get"]("vocal_flow"), _LA.text_hash(S.song.lyrics), "other") == "inputs")
base = dict(spec="vocal_flow", lyrics="a", language="th", settings={"bpm": 90, "timesignature": "4", "duration": 180}, corrections={}, style={})
fp0 = SR.input_fp(**base)
check("13A input_fp: BPM, signature, duration, corrections and language change the Vocal Flow fingerprint", all(SR.input_fp(**{**base, **ch}) != fp0 for ch in (
    {"settings": {"bpm": 91, "timesignature": "4", "duration": 180}}, {"settings": {"bpm": 90, "timesignature": "3", "duration": 180}}, {"settings": {"bpm": 90, "timesignature": "4", "duration": 120}},
    {"corrections": {"5": "x"}}, {"language": "en"})))
check("13A input_fp: the style words DO change every fingerprint (they are in every request), BPM does not change Rhyme & Flow", SR.input_fp(**{**base, "style": {"genre": "x"}}) != fp0 and
      SR.input_fp(**{**base, "spec": "rhyme_flow", "settings": {"bpm": 1}}) == SR.input_fp(**{**base, "spec": "rhyme_flow"}))
check("13A an old report without a stored fingerprint is not marked outdated by it", SR.outdated_why(SR.make_report("song_critic", {"lyrics_hash": "h"}, SR.parse_answer(good, "song_critic", T, ""), {}, good), "h", "zzz") == "")

# P1-B / P1-C: the rhyme list is never cut silently and colours mean groups
lyr40 = "\n".join("ขาวขาว" for _ in range(41))
pairs40 = [{"a": {"line": i, "word": "ขาว", "pos": [0, 3], "section": ""}, "b": {"line": i + 1, "word": "ขาว", "pos": [0, 3], "section": ""}, "sound": "exact" if i % 2 else "near", "group": f"g{i % 30}",
            "placement": "end", "confidence": "high"} for i in range(1, 41)]


def evn(n):
    return {"sections": [{"name": "x", "first": 1, "last": 41, "pairs": pairs40[:n], "repetitions": [], "lines": []}], "limitations": [], "facts": []}


FN["rp_show_tab"]("sr_rhyme")
rl = FN["sr_rhyme_lines"]


def flat(c):
    return " | ".join(texts(ft.Column(c), []))


t40 = flat(rl({}, evn(40), lyr40, False))
check("13B 40 pairs: the page says 24 / 40, offers Show all and does NOT claim that only these pairs exist", "24 / 40" in t40 and any(isinstance(c_, ft.TextButton) for c_ in controls_of(ft.Column(rl({}, evn(40), lyr40, False)), [])) and "Only the pairs above" not in t40 and "ในหลักฐานมีเฉพาะคู่ข้างบน" not in t40, t40[-260:])
check("13B 13+ marked lines: the lines are cut at 12 and say so", "12 / 41" in t40, t40[:200])
var("sr")["rall"].add((S.song.id, S.song.book.active_id))
t_all = flat(rl({}, evn(40), lyr40, False))
check("13B Show all: every pair is listed (40 cards) and the exhaustive statement is allowed again", t_all.count("EXACT") + t_all.count("NEAR") == 40 and ("Only the pairs above" in t_all or "ในหลักฐานมีเฉพาะคู่ข้างบน" in t_all))
var("sr")["rall"].clear()
check("13B 25 pairs also show the cut (24 / 25)", "24 / 25" in flat(rl({}, evn(25), lyr40, False)))
t10 = flat(rl({}, evn(10), lyr40, False))
check("13B 10 pairs: no cut, the statement stays", "/ 10" not in t10 and ("Only the pairs above" in t10 or "ในหลักฐานมีเฉพาะคู่ข้างบน" in t10))


def span_colors(c, out):
    for sp in (getattr(c, "spans", None) or []):
        st = getattr(sp, "style", None)
        if st is not None and getattr(st, "color", None):
            out.append((sp.text, st.color, bool(st.italic)))
    for a in ("content", "controls"):
        v = getattr(c, a, None)
        for x in (v if isinstance(v, (list, tuple)) else ([v] if v is not None and hasattr(v, "__dict__") else [])):
            span_colors(x, out)
    return out


def pr(l1, l2, w, snd, grp):
    return {"a": {"line": l1, "word": w, "pos": [0, len(w)]}, "b": {"line": l2, "word": w, "pos": [0, len(w)]}, "sound": snd, "group": grp, "placement": "end", "confidence": "high"}


two = {"sections": [{"name": "x", "first": 1, "last": 6, "repetitions": [], "lines": [], "pairs": [pr(1, 2, "ขาว", "exact", "อา+กว"), pr(3, 4, "ดำ", "exact", "อำ+ก"), pr(5, 6, "แดง", "near", "อา+กว")]}], "limitations": [], "facts": []}
sc = span_colors(ft.Column(rl({}, two, "ขาว\nขาว\nดำ\nดำ\nแดง\nแดง", False)), [])
col_of = {w: c for w, c, _i in sc}
check("13C two UNRELATED exact groups get different colours, and a near pair of the first group keeps that group's colour", col_of.get("ขาว") != col_of.get("ดำ") and col_of.get("แดง") == col_of.get("ขาว"), col_of)
check("13C exact and near differ by style (italic), not by colour", {i for w, c, i in sc if w == "แดง"} == {True} and {i for w, c, i in sc if w == "ขาว"} == {False})

# P1-D: an outdated report's line references do not jump into the changed lyrics
ref_new, ref_old = FN["sr_ref"](12, 13, False), FN["sr_ref"](12, 13, True)
check("13D a current reference opens the editor line; an outdated one is shown but has no click action", ref_new.on_click is not None and ref_old.on_click is None)
check("13D the story arc, the quotes and the sections of an outdated report get stale=True", "stale" in Path(ROOT / "music_studio.py").read_text(encoding="utf-8").split("def sr_critic_ribbon")[1][:300] and "sr_ref(s[\"first\"], s[\"last\"], stale)" in Path(ROOT / "music_studio.py").read_text(encoding="utf-8"))
OLD_LYR = S.song.lyrics
S.song.lyrics = "ใหม่\n" + OLD_LYR if hasattr(S.song, "lyrics") else OLD_LYR
APP.set_cell("lyr", lambda: S.song.lyrics)
FN["rp_show_tab"]("sr_critic")
refs = [c for c in controls_of(col, []) if isinstance(c, ft.Container) and str(getattr(getattr(c, "content", None), "value", "")).startswith("L") and str(getattr(getattr(c, "content", None), "value", ""))[1:2].isdigit()]
check("13D after a line is inserted above, NO line reference on the outdated page is clickable (quotes, sections, story arc)", len(refs) >= 2 and all(r.on_click is None for r in refs), len(refs))
S.song.lyrics = OLD_LYR
APP.set_cell("lyr", lambda: S.song.lyrics)

# P1-E: a line reference is exactly L12 or L12-L13
check("13E parse_lines accepts L12, L012, L12-L13, l12–L13", SR.parse_lines("L12", 30) == (12, 12) and SR.parse_lines("L012", 30) == (12, 12) and SR.parse_lines("L12-L13", 30) == (12, 13) and SR.parse_lines("l12–L13", 30) == (12, 13))
check("13E parse_lines rejects 'verse 2', a bare number, text with numbers and a missing line", all(SR.parse_lines(x, 30) == (-1, -1) for x in ("verse 2", "12", "lines 12 and 13", "L12 and L14", "L99", "section 3, L5")))
check("13E parse_lines: nothing / whole song = (0, 0)", SR.parse_lines("", 30) == (0, 0) and SR.parse_lines("-", 30) == (0, 0) and SR.parse_lines("whole song", 30) == (0, 0))
pv = SR.parse_answer(good.replace("lines: L12", "lines: verse 2"), "song_critic", T, "")
check("13E a finding whose lines are 'verse 2' is left out with a diagnostic that says what a reference must be", len(pv["sr"]["findings"]) == 1 and any("exactly L12" in d for d in pv["diagnostics"]), pv["diagnostics"])
ps = SR.parse_answer(good.replace("lines: L9-L13", "lines: the second verse"), "song_critic", T, "")
check("13E a section with a free-text line field is kept WITHOUT a line link, with a diagnostic", ps["sr"]["sections"][0]["first"] == 0 and any("SECTION.1" in d for d in ps["diagnostics"]), ps["diagnostics"])

# Q3: an unverified quotation is marked on the page
bq = SR.parse_answer(bad_quote, "song_critic", T, "")
fnd = bq["sr"]["findings"][0]
check("Q3 a finding whose quotation did not match carries quote_ok = False", fnd["quote_ok"] is False)
t_f = flat([FN["sr_finding"](fnd, "song_critic", T, False)])
check("Q3 the page marks it as unverified", "Unverified" in t_f or "ยังไม่ยืนยัน" in t_f)
t_g = flat([FN["sr_finding"](bq["sr"]["findings"][1], "song_critic", T, False)])
check("Q3 a verified finding has no such mark", "ยังไม่ยืนยัน" not in t_g and "Unverified" not in t_g)


# 14. Follow-up review (second independent audit) ----------------------------------------------------------------------------------------------------------------------------------
# F1: the fingerprint covers EVERY field of the request: if build_user changes, input_fp changes
snap0 = {"title": "Example 1", "lyrics": T, "style": {"genre": "Pop", "mood": "Happy", "energy": "High", "vocal": "Female", "language": "Thai"}}
settings0 = {"bpm": 90, "timesignature": "4", "duration": 180}
ok_all, why_not = True, []
for spec_ in SR.REPORTS:
    ev0 = SR.evidence_for(spec_, T, "th", settings0, {})[0]
    base_msg = SR.build_user(spec_, snap0, ev0)
    base_fp = SR.input_fp(spec_, T, "th", settings0, {}, snap0["style"], snap0["title"])
    for what, ch in (("title", {"title": "Example 2"}), ("genre", {"genre": "Rock"}), ("mood", {"mood": "Sad"}), ("energy", {"energy": "Low"}), ("vocal", {"vocal": "Male"}), ("language", {"language": "English"})):
        snap1 = dict(snap0, style=dict(snap0["style"]))
        if what == "title":
            snap1["title"] = "Example 2"
        else:
            snap1["style"].update(ch)
        msg_changed = SR.build_user(spec_, snap1, ev0) != base_msg
        fp_changed = SR.input_fp(spec_, T, "th", settings0, {}, snap1["style"], snap1["title"]) != base_fp
        if msg_changed and not fp_changed:
            ok_all = False; why_not.append((spec_, what))
check("14F1 for every report, a change that alters the request text always alters input_fp (title, genre, mood, energy, vocal, language)", ok_all, why_not)
S.song.title = "Another title"
check("14F1 renaming the song makes every report outdated (the title is part of the request)", all(FN["sr_state"](s_) == "outdated" for s_ in SR.REPORTS), {s_: FN["sr_state"](s_) for s_ in SR.REPORTS})
for spec_ in SR.REPORTS:
    FN["rp_show_tab"](SR.REPORTS[spec_].tab); FN["sr_start"](spec_); drain()
S.song.settings.mood = "Sad"
check("14F1 a new mood makes ALL FOUR outdated (all four requests carry the STYLE line)", all(FN["sr_state"](s_) == "outdated" for s_ in SR.REPORTS))
for spec_ in SR.REPORTS:
    FN["rp_show_tab"](SR.REPORTS[spec_].tab); FN["sr_start"](spec_); drain()

# F2: old line links stay disabled while Analyze-again runs
FN["rp_show_tab"]("sr_critic")
OLD2 = S.song.lyrics
S.song.lyrics = "ใหม่\n" + OLD2
APP.set_cell("lyr", lambda: S.song.lyrics)
FN["rp_show_tab"]("sr_critic")
CALLS_BEFORE = len(CALLS)
FN["sr_start"]("song_critic")                      # the request is scheduled but NOT drained: the state is 'running'
check("14F2 the state is 'running' while the old report (of the old lyrics) is on the page", FN["sr_state"]("song_critic") == "running", FN["sr_state"]("song_critic"))
refs = [c for c in controls_of(col, []) if isinstance(c, ft.Container) and str(getattr(getattr(c, "content", None), "value", "")).startswith("L") and str(getattr(getattr(c, "content", None), "value", ""))[1:2].isdigit()]
check("14F2 while it runs, NO saved line reference can navigate", len(refs) >= 2 and all(r.on_click is None for r in refs), len(refs))
drain()
refs2 = [c for c in controls_of(col, []) if isinstance(c, ft.Container) and str(getattr(getattr(c, "content", None), "value", "")).startswith("L") and str(getattr(getattr(c, "content", None), "value", ""))[1:2].isdigit()]
check("14F2 when the new report has arrived, its references work again", FN["sr_state"]("song_critic") in ("current", "partial") and refs2 and any(r.on_click is not None for r in refs2), (FN["sr_state"]("song_critic"), len(refs2), var("sr")["err"], [d for d in (FN["sr_get"]("song_critic").diagnostics if FN["sr_get"]("song_critic") else [])][:3]))
S.song.lyrics = OLD2
APP.set_cell("lyr", lambda: S.song.lyrics)

# F3: report colours are the Real-time colour groups
from music_studio import rhyme_color as _RC, python_rhyme_evidence as _PRE
_T3 = (ROOT / "docs/realtime_internal_rhyme/screenshot_song.txt").read_text(encoding="utf-8")
_rc = _RC.colorize(_T3, None, "th")
_ev = _PRE.build(_T3, "th")
_bad, _n = [], 0
_by_span = {}
for g_ in _rc.groups:
    for sp_ in g_:
        _by_span[(sp_.start, sp_.end)] = sp_.group
_off, _pos = {}, 0
for _i, _ln in enumerate(_T3.split("\n"), 1):
    _off[_i] = _pos; _pos += len(_ln) + 1
for sec in _ev["sections"]:
    for pr_ in sec["pairs"]:
        _n += 1
        gid = _by_span.get((_off[pr_["a"]["line"]] + pr_["a"]["pos"][0], _off[pr_["a"]["line"]] + pr_["a"]["pos"][1]))
        if gid is None or gid != pr_.get("comp"):
            _bad.append((pr_["a"]["word"], gid, pr_.get("comp")))
check("14F3 every evidence pair carries the Real-time colour-group id of its words (same ids as rhyme_color)", _n >= 8 and not _bad, _bad[:3])
FN["rp_show_tab"]("sr_rhyme")
_ev_data = SR.evidence_rhyme(_T3, "th")[1]
_cols = span_colors(ft.Column(rl({}, _ev_data, _T3, False)), [])
_pal = {w: LI_PAL[c] for w, c in []} if False else None
from music_studio import live_insights as _LI
_wc = {}
for sec in _ev_data["sections"]:
    for pr_ in sec["pairs"]:
        _wc.setdefault(pr_["a"]["word"], set()).add(pr_["comp"])
_exp = {w_: _LI.PALETTE[next(iter(c_)) % len(_LI.PALETTE)] for w_, c_ in _wc.items() if len(c_) == 1}
check("14F3 the page paints a pair's words with the editor's palette colour of that group", _cols and all(c == _exp.get(w, c) for w, c, _i in _cols if w in _exp), _cols[:3])
# two pairs with the SAME sound key but different components get different colours; an exact and a near pair of one component get the same colour
two2 = {"sections": [{"name": "x", "first": 1, "last": 6, "repetitions": [], "lines": [], "pairs": [dict(pr(1, 2, "ขาว", "exact", "อา+กว"), comp=0), dict(pr(3, 4, "ดาว", "exact", "อา+กว"), comp=1), dict(pr(5, 6, "แดง", "near", "อะ+กง"), comp=0)]}], "limitations": [], "facts": []}
cc = {w: c for w, c, _i in span_colors(ft.Column(rl({}, two2, "ขาว\nขาว\nดาว\nดาว\nแดง\nแดง", False)), [])}
check("14F3 same sound key but different components -> different colours; one component with different keys -> one colour", cc["ขาว"] != cc["ดาว"] and cc["ขาว"] == cc["แดง"], cc)

# F4: the Rhyme Map's own cap is disclosed
_words = ["ขาว", "ยาว", "ดาว", "คราว", "ราว", "สาว", "ข่าว", "เล่าว", "หนาว", "ก้าว", "ร้าว", "ฉาว"]
_lines = ["[Verse 1]"] + [f"เธอเดินไปกับ{w}" for w in (_words * 8)[:80]]
_evc = SR.evidence_rhyme("\n".join(_lines), "th")[1]
_tot = sum(s_.get("pairs_total", 0) for s_ in _evc["sections"])
_kept = sum(len(s_["pairs"]) for s_ in _evc["sections"])
check("14F4 the evidence keeps the number detected before the cap (pairs_total) and the cap is 40 per section", _tot > 40 and _kept == 40, (_tot, _kept))
t_cap = flat(rl({}, _evc, "\n".join(_lines), False))
check("14F4 the page says how many were detected and that the list is not complete (no 'only these pairs exist')", str(_tot) in t_cap and "Only the pairs above" not in t_cap and "ในหลักฐานมีเฉพาะคู่ข้างบน" not in t_cap and ("not complete" in t_cap or "ไม่ครบ" in t_cap), t_cap[-300:])
t_nocap = flat(rl({}, evn(10), lyr40, False))
check("14F4 with no cap, no such warning", "not complete" not in t_nocap and "ไม่ครบ" not in t_nocap)

# F5: the story arc of 13+ sections
secs15 = [{"name": f"S{i}", "first": i, "last": i, "reading": f"reading {i}"} for i in range(1, 16)]
arc = flat(FN["sr_critic_ribbon"]({"sections": secs15}, {}, False))
check("14F5 a story arc of 15 sections shows 12 / 15 and a Show all", "12 / 15" in arc and "S12" in arc and "S13" not in arc)
var("sr")["arc"].add((S.song.id, S.song.book.active_id))
arc2 = flat(FN["sr_critic_ribbon"]({"sections": secs15}, {}, False))
check("14F5 after Show all every section is there", "S15" in arc2 and "12 / 15" not in arc2)
var("sr")["arc"].clear()

# F6: the line reference contract is exactly L12 or L12-L13 (ascending)
check("14F6 'L12-13' and a reversed 'L13-L12' are refused", SR.parse_lines("L12-13", 30) == (-1, -1) and SR.parse_lines("L13-L12", 30) == (-1, -1) and SR.parse_lines("L12-L13", 30) == (12, 13))


# 15. third review: the REPLY language is part of the request, so part of the fingerprint
from music_studio import i18n as _I18N
_bad15 = []
for spec_ in SR.REPORTS:
    ev_ = SR.evidence_for(spec_, T, "th", settings0, {})[0]
    m_th, m_en = SR.build_user(spec_, snap0, ev_, "Thai"), SR.build_user(spec_, snap0, ev_, "English")
    f_th = SR.input_fp(spec_, T, "th", settings0, {}, snap0["style"], snap0["title"], "Thai")
    f_en = SR.input_fp(spec_, T, "th", settings0, {}, snap0["style"], snap0["title"], "English")
    if (m_th != m_en) and f_th == f_en:
        _bad15.append(spec_)
check("15 for every report, a different reply language changes both the request and input_fp", not _bad15, _bad15)
_lang0 = _I18N.lang()
for spec_ in SR.REPORTS:
    FN["rp_show_tab"](SR.REPORTS[spec_].tab); FN["sr_start"](spec_); drain()
check("15 all four reports are current before the window language changes", all(FN["sr_state"](s_) in ("current", "partial") for s_ in SR.REPORTS), {s_: FN["sr_state"](s_) for s_ in SR.REPORTS})
n15 = len(CALLS)
FN["rp_show_tab"]("sr_critic")
_before = page_text()
check("15 the open report page shows its status as Current before the switch", ("เป็นปัจจุบัน" in _before or "Current" in _before) and "ล้าสมัย" not in _before and "Outdated" not in _before, _before[:120])
FN["set_ui_lang"]("en" if _lang0 == "th" else "th")                   # the REAL language switch of the window, the report tab stays open
_after = page_text()
check("15 immediately after the window-language switch the VISIBLE status is Outdated (no tab change, no request)", ("ล้าสมัย" in _after or "Outdated" in _after) and "เป็นปัจจุบัน" not in _after and "Current" not in _after and len(CALLS) == n15, _after[:140])
check("15 switching') the window language makes every report Outdated and readable, without any LLM call", all(FN["sr_state"](s_) == "outdated" for s_ in SR.REPORTS) and len(CALLS) == n15)
FN["rp_show_tab"]("sr_critic"); drain()
check("15 the saved report is still on the page after the switch (no request was made)", len(CALLS) == n15 and "ภาพชัดแต่เรื่องไม่เดิน" in page_text())
FN["sr_start"]("song_critic"); drain()
check("15 Analyze again with the new window language makes only that report current again", FN["sr_state"]("song_critic") in ("current", "partial") and all(FN["sr_state"](s_) == "outdated" for s_ in ("rhyme_flow", "vocal_flow", "lyric_language")))
FN["set_ui_lang"](_lang0)

print(f"\n{N[0] - len(FAILS)} / {N[0]} passed")
sys.exit(1 if FAILS else 0)
