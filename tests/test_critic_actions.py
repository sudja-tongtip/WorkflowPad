"""Song Critic actions (Task C): the finding detail, Preview / Apply / Undo through the chat's own edit system, and 'Discuss with Songwriter' (context, session, budget). MOCK: no model is called; the LLM
functions are recorders. The UI harness builds the real window's control tree on a fake page: it is NOT the running app (see the screenshots of the real Flet app for that).
Run: python tests/test_critic_actions.py"""
from __future__ import annotations

import asyncio
import gc
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tests"))
sys.stdout.reconfigure(encoding="utf-8")

from _ui_harness import App
APP = App()
import flet as ft
from music_studio import i18n as I18N, llm_chat as LC, sheets as SH, spec_reports as SR, specialist_engine as SE, specialists as SP, store as ST

T = (ROOT / "docs/realtime_internal_rhyme/screenshot_song.txt").read_text(encoding="utf-8")
ROWS = T.split("\n")
SUNG = [n for n, r in enumerate(ROWS, 1) if r.strip() and not r.startswith("[")]
A, B, C2, D = SUNG[1], SUNG[2], SUNG[7], SUNG[9]
FAILS: list = []
N = [0]
CALLS: list = []                                  # (kind, system start, user) of every request


def check(name, ok, detail=""):
    N[0] += 1
    print(("PASS " if ok else "FAIL ") + name + (("  " + str(detail)[:170]) if detail else ""))
    if not ok:
        FAILS.append(name)


PROPOSAL = "ถ้อยคำใหม่ของบรรทัดนี้"


def critic_answer():
    return (f"[VERDICT.1]\nheadline: ภาพชัดแต่เรื่องไม่เดิน\nreading: อธิบายเหตุผล\n"
            f"[FINDING.1]\nkind: concern\npriority: high\nlines: L{A}, L{C2}\nquote: <<<\n{ROWS[A - 1]}\n>>>\nobservation: บรรทัดนี้กลับมาซ้ำ\ninterpretation: ผู้ฟังรู้สึกว่าไม่มีอะไรใหม่\n"
            f"recommendation: ให้ท่อนที่สองมีเรื่องใหม่หนึ่งเรื่อง\nkeep: ถ้าตั้งใจให้ติดอยู่กับความรู้สึกเดิมก็คงไว้ได้\n"
            f"[FINDING.2]\nkind: concern\npriority: medium\nlines: L{B}\nquote: <<<\n{ROWS[B - 1]}\n>>>\nobservation: บรรทัดนี้ตรงเกินไป\ninterpretation: ลดแรงสะเทือน\nrecommendation: ลองบอกให้น้อยลง\n"
            f"keep: เสียความชัดเจน\nproposal: <<<\n{PROPOSAL}\n>>>\n"
            f"[FINDING.3]\nkind: keep\npriority: -\nlines: the second verse\nquote: <<<\n{ROWS[D - 1]}\n>>>\nobservation: ใช้ได้อยู่แล้ว\ninterpretation: ทำหน้าที่ของมัน\nrecommendation: -\nkeep: คงไว้\n"
            f"[SECTION.1]\nname: Verse 1\nlines: L{A}-L{B}\nreading: เปิดภาพ\n[NOTE.1]\n- ไม่ได้ฟังเสียง\n[DONE]")


def fake_llm(system, user, *a, **k):
    kind = "critic" if "Song Critic AI" in system else "songwriter"
    CALLS.append((kind, system[:30], user, k.get("session")))
    return critic_answer() if kind == "critic" else "ตอบจากผู้ช่วยแต่งเพลง: เห็นด้วยบางส่วน"


LC.ask_local = lambda host, wf, system, user, *a, **k: fake_llm(system, user, *a, **k)
LC.ask_grok = lambda key, model, system, user, *a, **k: fake_llm(system, user, *a, **k)

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


song = ST.create_song("Critic actions", T)
SH.ensure_book(song)
APP.found["open_song"](song.id)
drain(); APP.refresh(); reindex()
S = var("S")
S.backend, S.host = "local:gemma4_llm.json", "127.0.0.1:8188"
col = var("sr_col")


def texts(c, out):
    if c is None:
        return out
    v = getattr(c, "value", None)
    if isinstance(v, str):
        out.append(v)
    for a in ("text", "content", "title", "subtitle"):
        t = getattr(c, a, None)
        if isinstance(t, str):
            out.append(t)
        elif t is not None and not isinstance(t, (int, float, bool)):
            texts(t, out)
    for t in (getattr(c, "spans", None) or []):
        texts(t, out)
    for k in (getattr(c, "controls", None) or []):
        texts(k, out)
    return out


def page_text():
    return " | ".join(texts(col, []))


def all_controls(c, out):
    out.append(c)
    for a in ("content", "controls"):
        v = getattr(c, a, None)
        for x in (v if isinstance(v, (list, tuple)) else ([v] if v is not None and hasattr(v, "__dict__") else [])):
            all_controls(x, out)
    return out


def buttons():
    return [c for c in all_controls(col, []) if isinstance(c, (ft.FilledButton, ft.OutlinedButton, ft.TextButton))]


def btn_text(b):
    return str(getattr(b, "content", None) or getattr(b, "text", None) or "")


def btns(label):
    return [b for b in buttons() if label in btn_text(b)]


def is_discuss(m):
    return m.role == "user" and (m.text.startswith(I18N.tr("Let's discuss this Song Critic finding")) or m.text.startswith(I18N.tr("Let's discuss the Song Critic report")))


def msgs():
    return list(S.chat)


# ── analyze once (a mock critic answer): the report holds a multi-reference finding, a finding with a proposal, and a finding with unreadable lines ─────────────
FN["rp_show_tab"]("sr_critic")
FN["sr_start"]("song_critic"); drain()
rep = FN["sr_get"]("song_critic")
fs = rep.data["sr"]["findings"]
check("0 the mock answer made a current report with three findings, none dropped", FN["sr_state"]("song_critic") in ("current", "partial") and len(fs) == 3, [x["id"] for x in fs])
check("0 finding 1 keeps its two separate references", fs[0]["refs"] == [[A, A], [C2, C2]], fs[0]["refs"])
check("0 finding 3 (unreadable lines) is kept, marked, without a link", fs[2]["lines_invalid"] == "the second verse" and fs[2]["refs"] == [] and fs[2]["first"] == 0)
check("0 finding 2 carries a proposal for its one reference", fs[1]["proposal"] == PROPOSAL, fs[1]["proposal"])
n_critic = sum(1 for c in CALLS if c[0] == "critic")

# ── U1..U3: the detail of a finding ────────────────────────────────────────────────────────────────────────────────────────────────────────
txt = page_text()
check("U1 a finding shows what was found, why it matters, the suggested action and the trade-off", all(x in txt for x in ("What was found", "Why it matters", "Suggested action", "Trade-off")) or all(x in txt for x in ("สิ่งที่พบ", "ทำไมจึงสำคัญ", "ข้อแลกเปลี่ยน")) or ("บรรทัดนี้กลับมาซ้ำ" in txt and "ผู้ฟังรู้สึกว่าไม่มีอะไรใหม่" in txt and "ให้ท่อนที่สองมีเรื่องใหม่หนึ่งเรื่อง" in txt and "ถ้าตั้งใจให้ติดอยู่กับความรู้สึกเดิมก็คงไว้ได้" in txt))
check("U1 the evidence: one line chip per separate reference (L%d and L%d) and the program's own lines" % (A, C2), f"L{A}" in txt and f"L{C2}" in txt and ROWS[A - 1].strip() in txt and ROWS[C2 - 1].strip() in txt, txt[:100])
check("U1 the unreadable-lines finding says so and is not given a link", ("Lines not readable" in txt or "อ่านเลขบรรทัดไม่ได้" in txt) and "the second verse" in txt)
check("U2 a conceptual finding offers Discuss with Songwriter and no Apply / Preview", len(btns("Discuss with Songwriter")) + len(btns("คุยต่อ")) >= 3 or len(btns("Songwriter")) >= 3, [btn_text(b) for b in buttons()][:8])
check("U3 the finding with a proposal shows ORIGINAL and PROPOSED and Preview + Apply", PROPOSAL in txt and ROWS[B - 1].strip() in txt and len([b for b in buttons() if "Preview" in btn_text(b) or "ตัวอย่าง" in btn_text(b)]) == 1 and len([b for b in buttons() if btn_text(b) == "Apply" or btn_text(b) == "นำไปใช้"]) == 1)
check("U3 only ONE Apply exists (the conceptual findings have none)", sum(1 for b in buttons() if btn_text(b) in ("Apply", "นำไปใช้")) == 1)

# ── U4..U6: Preview / Apply / Undo / Redo through the chat's own system ───────────────────────────────────────────────────────────────────
before = S.song.lyrics
prev = [b for b in buttons() if "Preview" in btn_text(b) or "ตัวอย่าง" in btn_text(b)][0]
prev.on_click(None)
check("U4 Preview does not change the lyrics", S.song.lyrics == before)
check("U4 Preview made the edit available in the Songwriter chat (the existing system), once", sum(1 for m in S.chat for e in (m.edits or []) if e.get("critic_key")) == 1)
prev.on_click(None)
check("U4 pressing Preview again does not make a second chat edit", sum(1 for m in S.chat for e in (m.edits or []) if e.get("critic_key")) == 1)
apply_btn = [b for b in buttons() if btn_text(b) in ("Apply", "นำไปใช้")][0]
apply_btn.on_click(None)
rows_after = S.song.lyrics.split("\n")
check("U5 Apply puts the proposed wording on exactly that line and changes nothing else", rows_after[B - 1] == PROPOSAL and rows_after[:B - 1] == ROWS[:B - 1] and rows_after[B:] == ROWS[B:], rows_after[B - 1])
check("U5 the page shows it as applied and offers no second Apply", ("Applied" in page_text() or "นำไปใช้แล้ว" in page_text()) and not [b for b in buttons() if btn_text(b) in ("Apply", "นำไปใช้")], (page_text()[-200:], [btn_text(b) for b in buttons()]))
FN["do_undo"]()
check("U6 Undo (the editor's own) restores the old lyrics", S.song.lyrics == before)
FN["sr_render"]()
check("U6 after Undo the proposal is open again (Preview / Apply are back)", len([b for b in buttons() if btn_text(b) in ("Apply", "นำไปใช้")]) == 1)
FN["do_redo"]()
check("U6 Redo applies it again", S.song.lyrics.split("\n")[B - 1] == PROPOSAL)
FN["do_undo"]()

# ── U7: outdated / changed lyrics: the old proposal cannot be applied from the report ───────────────────────────────────────────────────────
S.song.lyrics = "[Intro]\nบรรทัดใหม่ที่เพิ่มด้านบน\n" + before
APP.set_cell("lyr", lambda: S.song.lyrics)
FN["rp_show_tab"]("sr_critic")
check("U7 the report is outdated after the lyrics changed", FN["sr_state"]("song_critic") == "outdated")
check("U7 an outdated report offers NO Apply and NO Preview for its proposal, and says why", not [b for b in buttons() if btn_text(b) in ("Apply", "นำไปใช้") or "Preview" in btn_text(b)] and ("outdated" in page_text().lower() or "ล้าสมัย" in page_text()))
check("U7 its Discuss buttons still work (a discussion can start from an old report)", len([b for b in buttons() if "Songwriter" in btn_text(b) or "ผู้ช่วยแต่งเพลง" in btn_text(b)]) >= 3)
S.song.lyrics = before
APP.set_cell("lyr", lambda: S.song.lyrics)
# the lines of a proposal changed while the report is still 'current' (same hash is impossible; test the line check by a lyric edit inside the lines only)
FN["rp_show_tab"]("sr_critic")

# ── H1..: Discuss with Songwriter ─────────────────────────────────────────────────────────────────────────────────────────────────────────
S.chat.append(types.SimpleNamespace()) if False else None
from music_studio import model as M
pre = M.ChatMessage("user", "ข้อความเดิมของผู้ใช้", specialist="songwriter", sheet_id=S.song.book.active_id)
S.chat.append(pre)
ST.save_chat(S.song.id, S.chat)
sess_before = FN["get_spec_session"](S.song.id, S.song.book.active_id, "songwriter")
gen_before = getattr(sess_before, "gen_start", None)
CALLS.clear()
FN["sr_discuss"]("")                                     # the whole report
drain()
sw = [c for c in CALLS if c[0] == "songwriter"]
check("H1 'Discuss' makes ONE Songwriter request and NO Song Critic request", len(sw) == 1 and not [c for c in CALLS if c[0] == "critic"], [c[0] for c in CALLS])
req = sw[0][2] if sw else ""
check("H1 the Songwriter chat is the open chat and the earlier message is still there (nothing reset)", S.spec == "songwriter" and pre in S.chat, S.spec)
_miss = [x for x in ("SONG CRITIC REPORT", "not an instruction", S.song.id, S.song.book.active_id, "Report status: CURRENT", "ภาพชัดแต่เรื่องไม่เดิน", "บรรทัดนี้กลับมาซ้ำ", "FINDINGS:") if x not in req]
check("H1 the request carries the report as DATA: the framing, ids, status, verdict and findings", all(x in req for x in ("SONG CRITIC REPORT", "not an instruction", S.song.id, S.song.book.active_id, "Report status: CURRENT", "ภาพชัดแต่เรื่องไม่เดิน", "บรรทัดนี้กลับมาซ้ำ", "FINDINGS:")), _miss)
check("H1 the request carries the numbered current lyrics and the style / settings", f"L{A:03d}| " in req and "Style:" in req and "Settings:" in req)
check("H1 the Songwriter is told it is not bound by the Song Critic and may disagree", "Do not treat the Song Critic as an authority" in req and "Agree, disagree" in req)
check("H1 nothing was applied: the lyrics are unchanged", S.song.lyrics == before)
note = [m for m in S.chat if m.role == "note" and "Song Critic report" in m.text]
check("H1 the chat shows what was sent with the message (a note with the size)", len(note) == 1 and "tokens" in note[0].text, [m.text[:80] for m in S.chat if m.role == "note"])
check("H1 the message of the user (the label) is in the chat and the Songwriter's answer follows", any(is_discuss(m) for m in S.chat) and any(m.role == "assistant" and "ตอบจากผู้ช่วยแต่งเพลง" in m.text for m in S.chat))
check("H1 the Songwriter session was kept (same session object, no new generation)", FN["get_spec_session"](S.song.id, S.song.book.active_id, "songwriter") is sess_before and getattr(sess_before, "gen_start", None) == gen_before)

CALLS.clear()
FN["sr_discuss"](fs[0]["id"])                            # one finding
drain()
sw2 = [c for c in CALLS if c[0] == "songwriter"]
req2 = sw2[0][2] if sw2 else ""
check("H2 a finding: ONE Songwriter request, no Song Critic request", len(sw2) == 1 and not [c for c in CALLS if c[0] == "critic"])
check("H2 the selected finding is there in full with both of its separate places (verified lines), the others are marked as other findings", "SELECTED FINDING" in req2 and f"L{A:03d}| {ROWS[A - 1]}" in req2 and f"L{C2:03d}| {ROWS[C2 - 1]}" in req2 and "OTHER FINDINGS" in req2 and "Trade-off / keep" in req2, req2[:150])
check("H2 the lines BETWEEN the two places are not in the finding's evidence block", f"L{A + 1:03d}| {ROWS[A]}" not in req2.split("OTHER FINDINGS")[0].split("SELECTED FINDING")[1] if A + 1 != C2 else True)
check("H2 the earlier Songwriter message and the first discussion are still in the chat (history kept, session not reset)", pre in S.chat and sum(1 for m in S.chat if is_discuss(m)) == 2)
check("H2 the lyrics are unchanged and no Apply happened", S.song.lyrics == before)

# changed lyrics + outdated status
S.song.lyrics = before.replace(ROWS[A - 1], "บรรทัดที่แก้แล้ว", 1)
APP.set_cell("lyr", lambda: S.song.lyrics)
CALLS.clear()
FN["sr_discuss"](fs[0]["id"]); drain()
req3 = [c for c in CALLS if c[0] == "songwriter"][0][2]
check("H3 an outdated report says OUTDATED, what changed and that its line numbers belong to the old lyrics", "Report status: OUTDATED" in req3 and "Changes since the analysis" in req3 and "OLD lyrics" in req3 and f"L{A}" in req3.split("Changes since the analysis")[1][:80], req3[req3.find("Report status"):][:300])
check("H3 the current lyrics (with the change) are what travels", "บรรทัดที่แก้แล้ว" in req3)
S.song.lyrics = before
APP.set_cell("lyr", lambda: S.song.lyrics)

# the context budget
big = {"sections": [{"name": f"S{i}", "first": 2, "last": 3, "reading": "ท" * 250} for i in range(30)], "findings": [], "headline": "h" * 300, "reading": "r" * 900, "notes": ["n" * 200] * 6}
for i in range(4):
    big["findings"].append({"id": f"so{i}", "kind": "concern", "priority": "high", "first": 2, "last": 3, "refs": [[2, 2], [3, 3]], "quote": "q", "quote_ok": True, "observation": "o" * 900, "interpretation": "i" * 900, "recommendation": "r" * 900, "keep": "k" * 900})
many = "\n".join(f"บรรทัดที่ {i} ของเพลงยาวมาก ๆ เพื่อทดสอบงบ" for i in range(1, 400))
res = {}
for b in (3500, 1500, 700):
    t_, inf = SR.handoff_text(title="t", song_id="s", sheet_id="h", style={}, settings={}, sr_data=big, rep_ts="2026-01-01 00:00", backend="b", model="m", state="current", why="", lyrics_then=many, lyrics_now=many, finding_id="so1", budget_tokens=b)
    res[b] = (t_, inf)
check("H4 a long report and long lyrics are cut to the budget in order of priority; the text says what was cut", all(res[b][1]["tokens"] <= b + 450 for b in res) and res[3500][1]["tokens"] >= res[700][1]["tokens"], {b: res[b][1] for b in res})
check("H4 the selected finding survives every budget (full, or in its short form when nothing else fits)", all("SELECTED FINDING" in res[b][0] for b in res))
check("H4 when something was left out the message says so by name (CONTEXT NOTE) and the info lists it", "CONTEXT NOTE" in res[700][0] and res[700][1]["left_out"] and all(x in res[700][0] for x in res[700][1]["left_out"]))
check("H4 the numbered lyrics are the first thing cut, never the selected finding", "the numbered lyrics" in (res[1500][1]["left_out"] + res[1500][1]["shortened"]) or "the numbered lyrics" in (res[700][1]["left_out"] + res[700][1]["shortened"]))
old_ctx = dict(LC.CTX_OVERRIDE)
songb = ST.create_song("Budget song", T)
SH.ensure_book(songb)
APP.found["open_song"](songb.id); drain(); APP.refresh()
S = var("S")
S.backend, S.host = "local:gemma4_llm.json", "127.0.0.1:8188"
col = var("sr_col")
FN["rp_show_tab"]("sr_critic"); FN["sr_start"]("song_critic"); drain()
repb = FN["sr_get"]("song_critic")
fsb = repb.data["sr"]["findings"]
LC.CTX_OVERRIDE["local"] = 15000                         # a smaller local context: the budget is what the conversation can still hold, so the block is shorter than usual
CALLS.clear()
FN["sr_discuss"](fsb[0]["id"]); drain()
swb = [c for c in CALLS if c[0] == "songwriter"]
noteb = [m for m in S.chat if m.role == "note" and "Song Critic report" in m.text]
check("H4 on a 15 000-token local context the hand-off is sent with a budget below the 3 500 cap and the chat note states the budget", len(swb) == 1 and noteb and "-token budget" in noteb[-1].text and "3500-token" not in noteb[-1].text, noteb[-1].text[:160] if noteb else "")
n_sw = len([c for c in CALLS if c[0] == "songwriter"])
LC.CTX_OVERRIDE["local"] = 12000                         # a context the conversation no longer fits: the engine's own guard refuses, NOTHING is sent to the model
n_chat_before = len(S.chat)
CALLS.clear()
FN["sr_discuss"]("")
drain()
check("H4 when the conversation has no room the Discuss is refused BEFORE anything is sent or written: no model request, no chat message, no pending report, lyrics unchanged", not [c for c in CALLS if c[0] == "songwriter"] and len(S.chat) == n_chat_before and not var("handoff_pending") and S.song.lyrics == songb.lyrics, (len(S.chat), n_chat_before))
LC.CTX_OVERRIDE.update(old_ctx)
APP.found["open_song"](song.id); drain(); APP.refresh()
S = var("S")
S.backend, S.host = "local:gemma4_llm.json", "127.0.0.1:8188"
col = var("sr_col")

# another song: the pending report is not used
song2 = ST.create_song("Other song", "[Verse 1]\nบรรทัดของอีกเพลง\nอีกบรรทัด")
SH.ensure_book(song2)
FN["sr_discuss"](fs[0]["id"])                            # pending for the song that is open now
pend = dict(var("handoff_pending"))
APP.found["open_song"](song2.id); drain(); APP.refresh()
CALLS.clear()
S = var("S")
S.backend, S.host = "local:gemma4_llm.json", "127.0.0.1:8188"
drain()
swo = [c for c in CALLS if c[0] == "songwriter"]
check("H5 the pending report of song 1 is keyed to its own song and sheet: a message in song 2 carries no Song Critic report", not any("SONG CRITIC REPORT" in c[2] for c in swo) or all(song2.id in c[2] for c in swo), len(swo))
FN["sr_discuss"]("")
check("H5 in song 2 (no report) Discuss refuses with a message and sends nothing", not [c for c in CALLS if c[0] == "songwriter" and "SONG CRITIC REPORT" in c[2]])
APP.found["open_song"](song.id); drain(); APP.refresh()
S = var("S")
S.backend, S.host = "local:gemma4_llm.json", "127.0.0.1:8188"

# the page layout: nothing in a finding card has a fixed width wider than the narrowest panel (320 px minus padding)
FN["rp_show_tab"]("sr_critic")
wide = [c for c in all_controls(col, []) if isinstance(getattr(c, "width", None), (int, float)) and c.width > 280]
check("L1 no control of the report page has a fixed width above 280 px (the drawer is 320-560 px wide; real-Flet screenshots are in SCREENSHOTS/)", not wide, [type(c).__name__ for c in wide][:3])
rows_wrap = [c for c in all_controls(col, []) if isinstance(c, ft.Row) and c.wrap]
check("L1 the button rows wrap (they stack in a narrow panel)", len(rows_wrap) >= 3, len(rows_wrap))

# legacy report: a finding saved before refs existed still renders and discusses
leg = {"id": "so9", "kind": "concern", "priority": "high", "first": A, "last": A, "quote": ROWS[A - 1], "quote_ok": True, "observation": "legacy obs", "interpretation": "legacy why", "recommendation": "legacy do", "keep": ""}
legacy_ctrl = FN["sr_finding"](leg, "song_critic", T, False)
check("L2 a legacy finding (first / last only, no refs, no proposal key) renders with its line chip", f"L{A}" in " | ".join(texts(legacy_ctrl, [])) and "legacy obs" in " | ".join(texts(legacy_ctrl, [])))
t_leg, inf_leg = SR.handoff_text(title="t", song_id="s", sheet_id="h", style={}, settings={}, sr_data={"headline": "h", "reading": "r", "findings": [leg], "sections": [], "notes": []}, rep_ts="x", backend="b", model="m", state="current", why="", lyrics_then=T, lyrics_now=T, finding_id="so9")
check("L2 a legacy finding can be handed off", "legacy obs" in t_leg and f"L{A}" in t_leg)


# ── 16. second review: old line numbers over changed lyrics, and a HARD token budget ─────────────────────────────────────────────────────────────────
srd = {"headline": "h", "reading": "r", "notes": [], "sections": [], "findings": [{"id": "so1", "kind": "concern", "priority": "high", "first": 24, "last": 26, "refs": [[24, 26]], "quote": "q", "quote_ok": True,
       "observation": "obs", "interpretation": "why", "recommendation": "do", "keep": "cost"}]}
base_l = "\n".join(f"บรรทัดที่ {i} ของเพลง" for i in range(1, 60))


def ho(then, now, b=3500, state="current", why=""):
    return SR.handoff_text(title="t", song_id="s", sheet_id="h", style={}, settings={}, sr_data=srd, rep_ts="x", backend="b", model="m", state=state, why=why, lyrics_then=then, lyrics_now=now, finding_id="so1", budget_tokens=b)


t_same, _i = ho(base_l, base_l)
check("16A no change: the lyrics around the finding are built from its (valid) numbers", "CURRENT LYRICS AROUND THE FINDING" in t_same and "L022| บรรทัดที่ 22" in t_same and "L028| บรรทัดที่ 28" in t_same)
ins10 = "\n".join(f"บรรทัดที่เพิ่ม {i}" for i in range(1, 11)) + "\n" + base_l              # ten lines inserted at the start: old L24-L26 are now L34-L36
t_ins, _i = ho(base_l, ins10, state="outdated", why="lyrics")
check("16A ten lines inserted above: NO 'lyrics around the finding' block is built from old numbers (it would show unrelated lines as the finding's place)", "AROUND THE FINDING" not in t_ins, t_ins[t_ins.find("SELECTED"):][:200])
check("16A the selected finding's evidence is labelled as the OLD lyrics' numbering, with the old lines as they were at the analysis", "numbered as in the OLD lyrics of the analysis" in t_ins and "L024| บรรทัดที่ 24 ของเพลง" in t_ins)
check("16A the full CURRENT lyrics (numbered as they are now) still travel when there is room", "L034| บรรทัดที่ 24 ของเพลง" in t_ins)
t_del, _i = ho(base_l, "\n".join(base_l.split("\n")[10:]), state="outdated", why="lyrics")
check("16A ten lines deleted above: no around-block either; OUTDATED and the change are stated", "AROUND THE FINDING" not in t_del and "Report status: OUTDATED" in t_del and "Changes since the analysis" in t_del)
t_edit, _i = ho(base_l, base_l.replace("บรรทัดที่ 25 ของเพลง", "บรรทัดที่ 25 แก้แล้ว"), state="outdated", why="lyrics")
check("16A one line edited in place: the around-block is NOT built from the report's numbers (the lyrics are not the same)", "AROUND THE FINDING" not in t_edit and "บรรทัดที่ 25 แก้แล้ว" in t_edit and "numbered as in the OLD lyrics" in t_edit)
bad = []
for b in (500, 700, 1500, 3500):
    for st in ("current", "outdated"):
        tx, inf = ho(base_l, base_l if st == "current" else ins10, b, st, "lyrics" if st == "outdated" else "")
        if tx is not None and LC.estimate_tokens(tx) > b:
            bad.append((b, st, LC.estimate_tokens(tx)))
check("16B the budget is a HARD limit: for 500 / 700 / 1500 / 3500 the final text (note included) never exceeds it", not bad, bad)
feasible = [(b, ho(base_l, base_l, b)) for b in (700, 1500, 3500)]
check("16B where a hand-off fits, the selected finding and its verified evidence survive", all(tx is not None and "SELECTED FINDING" in tx and "obs" in tx for b, (tx, inf) in feasible))
t300, i300 = ho(base_l, base_l, 300)
check("16B a budget below the smallest useful hand-off is REFUSED (None + reason), not padded to a floor, and reports the size it would need", t300 is None and i300.get("refused") and i300["tokens"] > 300 and i300["budget"] == 300, i300)
small = ho(base_l, base_l, 700)[1]
check("16B a tight budget still names what was shortened / left out, inside the budget", small["tokens"] <= 700 and (small["shortened"] or small["left_out"]), small)

print(f"\n{N[0] - len(FAILS)} / {N[0]} passed")
sys.exit(1 if FAILS else 0)
