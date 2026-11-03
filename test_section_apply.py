"""Selection snapshot and section apply. Test lyrics only: no song library, no LLM, no editor engine.

Run: python test_section_apply.py
"""
from __future__ import annotations

import asyncio
import sys
import time
import traceback
from pathlib import Path

import flet as ft
from flet_code_editor import CodeEditor, CodeLanguage

from music_studio import chat_selection as CS
from music_studio import i18n as I18N
from music_studio import lyric_actions as AC
from music_studio import lyric_review as LR
from music_studio import llm_chat as LC
from music_studio import section_apply as SA
from music_studio import specialist_engine as SE

OUT = Path(__file__).resolve().parent / "_sel_preview_shots"
SONG = "[Verse 1]\nhello\n\n[Breakdown]\nold line\nstill old\n\n[Chorus]\nhook\n[Chorus]\nhook again\n"
BODY = "old line\nstill old"
PROP = "[Breakdown]\nnew line\nstill new"
PROP_SOFT = "[Breakdown - soft]\nnew line\nstill new"
PROP_BRIDGE = "[Bridge]\nbrand new"
QUESTION = "ปรับเฉพาะท่อนที่เลือก"
META = {"genre": "pop", "mood": "warm", "language": "en", "bpm": 90, "energy": "mid", "vocal": "female"}
FAILS: list[str] = []
RESULT: dict = {}


def check(name: str, ok: bool, detail: str = "") -> None:
    line = ("PASS  " if ok else "FAIL  ") + name + (("  " + detail) if detail else "")
    print(line, flush=True)
    if not ok:
        FAILS.append(line)


def reply(proposal: str) -> str:
    return LC.SEP["REPLY"] + "\nปรับท่อนนี้\n\n" + LC.SEP["PROPOSAL"] + "\n" + proposal + "\n\n" + LC.SEP["END"] + "\n"


def edit_history():
    src = Path(__file__).resolve().parent.joinpath("music_studio.py").read_text(encoding="utf-8")
    start = src.index("class EditHistory:")
    end = src.index("\nlog = logging.getLogger", start)
    ns = {"time": time}
    exec(src[start:end], ns)
    return ns["EditHistory"]


def counts(text: str) -> tuple:
    return text.count("[Breakdown]"), text.count("[Chorus]"), text.count("[Verse 1]"), "hook again" in text, "hello" in text


def logic() -> None:
    a = SONG.index(BODY)
    b = a + len(BODY)
    ha = SONG.index("[Breakdown]")
    hb = SONG.index("\n\n[Chorus]")
    whole = SONG[ha:hb]

    na, nb, repl = SA.prepare_replacement(SONG, a, b, PROP)
    lyrics_only = SONG[:na] + repl + SONG[nb:]
    check("lyrics-only apply keeps one Breakdown", lyrics_only.count("[Breakdown]") == 1 and "new line" in lyrics_only and "still old" not in lyrics_only, lyrics_only)
    check("lyrics-only apply keeps both Choruses and the Verse", counts(lyrics_only) == (1, 2, 1, True, True))

    na, nb, repl = SA.prepare_replacement(SONG, ha, hb, PROP_SOFT)
    whole_new = SONG[:na] + repl + SONG[nb:]
    check("whole section replaces the header in bounds", "[Breakdown - soft]" in whole_new and whole_new.count("[Breakdown]") == 0 and counts(whole_new)[1:] == (2, 1, True, True), whole_new)

    na, nb, repl = SA.prepare_replacement(SONG, a, b, PROP_BRIDGE)
    bridged = SONG[:na] + repl + SONG[nb:]
    check("a new Bridge is kept and the old header stays", bridged.count("[Bridge]") == 1 and bridged.count("[Breakdown]") == 1 and counts(bridged)[1:] == (2, 1, True, True))

    echoed = "[Verse 1]\nhello\n\n[Breakdown]\nold line\nstill old\n\n[Breakdown]\nnew line\nstill new\n\n[Chorus]\nhook\n[Chorus]\nhook again\n"
    folded = SA.fold_echoed_sections(SONG, echoed)
    check("full-song echo is not a second Breakdown", folded.count("[Breakdown]") == 1 and "new line" in folded and counts(folded)[1:] == (2, 1, True, True), folded)
    two = "[Chorus]\nhook\n[Chorus]\nhook\n"
    check("two identical Choruses stay", SA.fold_echoed_sections(two, two).count("[Chorus]") == 2)
    check("an extra identical Chorus is the only one removed", SA.fold_echoed_sections(two, two + "[Chorus]\nhook\n").count("[Chorus]") == 2)

    held = "[Breakdown]\n[Hold] ooo\nold line\nstill old\n"
    hs, he = held.index(BODY), held.index(BODY) + len(BODY)
    _a, _b, hrepl = SA.prepare_replacement(held, hs, he, PROP)
    held_out = held[:_a] + hrepl + held[_b:]
    check("[Hold] is not a section and stays", "[Hold] ooo" in held_out and held_out.count("[Breakdown]") == 1, held_out)

    snap = CS.capture_selection("s1", "sh1", SONG, (a, b))
    check("snapshot stores song, sheet, offsets and the slice", snap == {"song_id": "s1", "sheet_id": "sh1", "start": a, "end": b, "text": BODY})
    check("caret is not a snapshot", CS.capture_selection("s1", "sh1", SONG, (a, a)) is None)
    check("this song and sheet attach the slice once", CS.selection_from_snapshot("s1", "sh1", SONG, snap, None) == (True, BODY, a, b))
    check("another song attaches nothing", CS.selection_from_snapshot("s2", "sh1", SONG, snap, None) == (False, "", 0, 0))
    check("another sheet attaches nothing", CS.selection_from_snapshot("s1", "sh2", SONG, snap, None) == (False, "", 0, 0))
    moved = SONG.replace(BODY, "other line\nstill other")
    check("a changed slice attaches nothing", CS.selection_from_snapshot("s1", "sh1", moved, snap, None)[0] is False)
    key = ("s1", "sh1", a, b, BODY)
    check("× attaches nothing", CS.selection_from_snapshot("s1", "sh1", SONG, snap, key)[0] is False)
    other_song = "คนละเพลง"
    check("× does not block another song", CS.selection_for_chat("s2", "sh9", other_song, (0, len("คนละ")), key) == (True, "คนละ"))
    thai = "[Verse 1]\nฉันรอเธอ\n"
    t0 = thai.index("รอเธอ")
    tsnap = CS.capture_selection("s1", "sh1", thai, (t0, t0 + len("รอเธอ")))
    check("Thai snapshot round-trips", CS.selection_from_snapshot("s1", "sh1", thai, tsnap, None)[1] == "รอเธอ")

    sw = LC.build_user_message("Test", META, SONG, BODY, QUESTION, [], LC.selection_where(SONG, a, b))
    sw_block = sw.split("SELECTED TEXT (", 1)[1].split("USER REQUEST:", 1)[0]
    check("songwriter sends the whole sheet once", sw.count("CURRENT LYRICS:") == 1 and "hello" in sw and "hook again" in sw)
    check("songwriter selection block once", sw.count("SELECTED TEXT (") == 1 and sw_block.count(BODY) == 1 and "FOCUS ON THIS SELECTION" in sw and "only as context" in sw)
    check("songwriter question is not rewritten", sw.split("USER REQUEST:\n", 1)[1] == QUESTION)
    check("songwriter history is not attached", "RECENT CHAT:" not in sw)
    lines = (SONG.count("\n", 0, a) + 1, SONG.count("\n", 0, b) + 1)
    pr = SE.architect_user_message("Test", META, SONG, QUESTION, lines, BODY)
    pr_block = pr.split("SELECTED TEXT (", 1)[1].split("USER REQUEST:", 1)[0]
    check("producer line hint once", pr.count("THE USER HAS LINES") == 1)
    check("producer selection block once", pr.count("SELECTED TEXT (") == 1 and pr_block.count(BODY) == 1 and "FOCUS ON THIS SELECTION" in pr and "only as context" in pr)
    check("producer question is not rewritten", pr.split("USER REQUEST:\n", 1)[1] == QUESTION)
    check("producer still has the whole sheet", "hello" in pr and pr.count("hook again") >= 1)
    bare = SE.architect_user_message("Test", META, SONG, QUESTION, lines)
    check("producer without the slice keeps the old line hint", bare.count("THE USER HAS LINES") == 1 and "SELECTED TEXT (" not in bare and "Only relevant when the request says" in bare)

    o = SE.handle_chat_answer("songwriter", reply(PROP), SONG, BODY, True, QUESTION, False, (a, b))
    check("chat lyrics-only proposal drops the echoed header", o.proposal == "new line\nstill new", repr(o.proposal))
    o_soft = SE.handle_chat_answer("songwriter", reply(PROP_SOFT), SONG, whole, True, QUESTION, False, (ha, hb))
    check("chat whole-section proposal is offered", o_soft.proposal == PROP_SOFT, repr(o_soft.proposal) + " " + str(o_soft.notes))
    wide = SONG
    dropped = SONG.replace("hello", "HELLO").replace("[Chorus]\nhook again\n", "")
    o_drop = SE.handle_chat_answer("songwriter", reply(dropped), wide, wide, True, QUESTION, False, (0, len(wide)))
    check("a proposal that drops a Chorus is not offered", o_drop.proposal == "" and any("Chorus" in n for n in o_drop.notes), str(o_drop.notes))

    o_echo = SE.handle_chat_answer("songwriter", reply(echoed), SONG, "", False, QUESTION, False, None)
    check("full-song chat folds the echoed Breakdown", o_echo.proposal.count("[Breakdown]") == 1 and "new line" in o_echo.proposal and o_echo.proposal.count("[Chorus]") == 2, repr(o_echo.proposal))
    o_part = SE.handle_chat_answer("songwriter", reply(PROP), SONG, "", False, QUESTION, False, None)
    check("a partial Breakdown merges and does not duplicate", o_part.proposal.count("[Breakdown]") == 1 and "new line" in o_part.proposal and o_part.proposal.count("[Chorus]") == 2 and "[Breakdown -" not in o_part.proposal, repr(o_part.proposal))
    o_new = SE.handle_chat_answer("songwriter", reply(PROP_BRIDGE), SONG, "", False, QUESTION, False, None)
    check("a new Bridge is added to the song", "[Bridge]" in o_new.proposal and o_new.proposal.count("[Breakdown]") == 1 and o_new.proposal.count("[Chorus]") == 2, repr(o_new.proposal))

    digest = AC.text_hash(SONG)
    plan = AC.plan_apply("selection", SONG, PROP, BODY, a, b, digest, b)
    check("selection Apply is direct and has one Breakdown", "direct" in plan and plan["direct"][0].count("[Breakdown]") == 1 and plan["direct"][0].count("[Chorus]") == 2)
    plan_soft = AC.plan_apply("selection", SONG, PROP_SOFT, whole, ha, hb, digest, hb)
    check("whole-section Apply writes the new header only inside the range", "direct" in plan_soft and plan_soft["direct"][0] == whole_new)
    only_header = AC.plan_apply("selection", SONG, "[Breakdown]", BODY, a, b, digest, b)
    check("a header with no new lines applies nothing", only_header.get("options") == [] and "section header" in only_header.get("msg", ""))
    cursor = SONG.index("still old") + len("still old")
    ins = AC.plan_apply("insert", SONG, PROP, "", cursor, cursor, digest, cursor)
    check("insert strips a leading copy of the current section", "direct" in ins and ins["direct"][0].count("[Breakdown]") == 1 and "new line" in ins["direct"][0])
    ins_b = AC.plan_apply("insert", SONG, PROP_BRIDGE, "", cursor, cursor, digest, cursor)
    check("insert keeps a new Bridge", "direct" in ins_b and ins_b["direct"][0].count("[Bridge]") == 1 and ins_b["direct"][0].count("[Breakdown]") == 1)
    all_plan = AC.plan_apply("all", SONG, echoed, SONG, 0, len(SONG), digest, 0)
    check("whole-song Apply uses the folded text", "direct" in all_plan and all_plan["direct"][0].count("[Breakdown]") == 1 and all_plan["direct"][0].count("[Chorus]") == 2)

    edits_text = (
        "EDIT\ntarget: lines 5-6\noriginal: <<<\nold line\nstill old\n>>>\nreplacement: <<<\n[Breakdown]\nnew line\nstill new\n>>>\nreason: fresher\n"
        "EDIT\ntarget: line 9\noriginal: <<<\nhook\n>>>\nreplacement: <<<\nHOOK\n>>>\nreason: louder\n"
    )
    edits, notes = SE.parse_edits(edits_text, SONG, "songwriter")
    check("preview fits the echoed header away", len(edits) == 2 and notes == [] and "[Breakdown]" not in edits[0]["replacement"] and edits[0]["replacement"] == "new line\nstill new", str(notes))
    sec_text = "EDIT\ntarget: section Breakdown\noriginal: <<<\n>>>\nreplacement: <<<\n[Breakdown]\nnew line\nstill new\n>>>\nreason: fresher\n"
    sec_edits, sec_notes = SE.parse_edits(sec_text, SONG, "songwriter")
    check("section target fills the body and strips the echoed header", len(sec_edits) == 1 and sec_edits[0]["original"] == BODY and "[Breakdown]" not in sec_edits[0]["replacement"], str(sec_notes) + repr(sec_edits))

    def apply_all(text: str, items: list) -> str:
        for e in items:
            got = SE.apply_edit(e, text, "songwriter")
            if isinstance(got, str):
                raise AssertionError(got)
            text = got[0]
        return text

    one = apply_all(SONG, [edits[0]])
    both = apply_all(SONG, edits)
    report = SE.apply_change(LR.Item("e", "edit", edits[0]["first"], edits[0]["last"], edits[0]["original"], edits[0]["replacement"], ""), SONG, "songwriter")
    review = LR.apply_to(LR.Item("e", "edit", 5, 6, BODY, PROP, ""), SONG)
    check("Accept and Accept All share the Breakdown result", one == report[0] == review[0] and one.count("[Breakdown]") == 1 and one.count("[Chorus]") == 2)
    check("Accept All also rewrites the first Chorus only", both.count("[Chorus]") == 2 and "HOOK" in both and "hook again" in both and both.count("[Breakdown]") == 1, both)
    prod = SE.apply_change(LR.Item("e", "edit", 5, 6, BODY, "[Breakdown]\n" + BODY, ""), SONG, "music_producer")
    check("producer Apply does not strip a header", isinstance(prod, tuple) and prod[0].count("[Breakdown]") == 2, prod if isinstance(prod, str) else prod[0])
    song_same = SE.apply_change(LR.Item("e", "edit", 5, 6, BODY, "[Breakdown]\n" + BODY, ""), SONG, "songwriter")
    check("songwriter Apply strips that echoed header", isinstance(song_same, tuple) and song_same[0].count("[Breakdown]") == 1 and song_same[0] == SONG, song_same if isinstance(song_same, str) else song_same[0])

    Hist = edit_history()
    hist = Hist()
    cur, sel = SONG, (a, b)
    hist.record(cur, sel, burst=False, now=1)
    cur, sel = one, (a, a + len("new line\nstill new"))
    hist.record(cur, sel, burst=False, now=2)
    cur = both
    back = hist.step_back(cur, (0, 0))
    check("undo restores the lyrics from before Accept All", back[0] == one)
    back2 = hist.step_back(back[0], back[1])
    check("undo again restores the original song", back2[0] == SONG and back2[0].count("[Breakdown]") == 1)
    fwd = hist.step_forward(back2[0], back2[1])
    check("redo restores the first Accept", fwd[0] == one)

    copied = SONG[a:b]
    deleted = SONG[:a] + SONG[b:]
    check("copy reads the slice and delete removes only that slice", copied == BODY and deleted.count("[Breakdown]") == 1 and deleted.count("[Chorus]") == 2 and "old line" not in deleted)
    check("delete makes the old snapshot attach nothing", CS.selection_from_snapshot("s1", "sh1", deleted, snap, None)[0] is False)

    RESULT.update(before=SONG, after=plan["direct"][0], preview=edits[0]["replacement"], accept_all=both, undo=back2[0], redo=fwd[0],
                  songwriter=sw, producer=pr, a=a, b=b, snap=snap)


def find(root, key: str):
    if getattr(root, "key", None) == key:
        return root
    for child in list(getattr(root, "controls", None) or []):
        got = find(child, key)
        if got is not None:
            return got
    content = getattr(root, "content", None)
    if content is not None and content is not root:
        return find(content, key)
    return None


def u16(text: str, i: int) -> int:
    return len(text[:i].encode("utf-16-le")) // 2


def py(text: str, o: int) -> int:
    acc = 0
    for i, ch in enumerate(text):
        if acc >= o:
            return i
        acc += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


def main(page: ft.Page):
    page.title = "Section apply"
    page.bgcolor = "#0A1220"
    page.padding = 12
    page.theme_mode = ft.ThemeMode.DARK
    page.window.width = 1100
    page.window.height = 920
    I18N.install(ft)
    I18N.set_lang("th")

    state = {"song": "s1", "sheet": "sh1", "lyrics": SONG, "sel": (RESULT["a"], RESULT["b"]), "dismissed": None}

    def on_clear(e):
        text = state["lyrics"]
        a, b = state["sel"]
        if 0 <= a < b <= len(text):
            state["dismissed"] = (state["song"], state["sheet"], a, b, text[a:b])
        show()

    bar, apply = CS.make_chat_selection_bar(on_clear, accent_soft="#173766", text1="#E7EEFA", text2="#98A8C4", border="#3A6294")
    chat = ft.TextField(value="", hint_text="Ask the AI...", multiline=True, min_lines=1, max_lines=2, text_size=12,
                        bgcolor="#0C1628", color="#E7EEFA", border_color="#223352")
    code = CodeEditor(value=SONG, language=CodeLanguage.PLAINTEXT, autocomplete=False, height=150)
    status = ft.Text("", size=11, color="#B7C7E2", selectable=True)

    def show():
        has, text = CS.selection_for_chat(state["song"], state["sheet"], state["lyrics"], state["sel"], state["dismissed"])
        apply(text)
        return has, text

    def box(title: str, value: str):
        return ft.Column([ft.Text(title, size=11, color="#7F90B0"),
                          ft.Text(value, size=12, color="#E7EEFA", selectable=True, font_family="Consolas")], spacing=2, tight=True)

    before = box("Before", RESULT["before"])
    after = box("After selection Apply", RESULT["after"])
    preview = box("Preview (fitted edit)", RESULT["preview"])
    accepted = box("Accept All", RESULT["accept_all"])
    undo = box("Undo back to original", RESULT["undo"])
    panels = ft.Column([before, after, preview, accepted, undo, status], spacing=8, tight=True, scroll=ft.ScrollMode.AUTO)
    chat_col = ft.Container(width=260, bgcolor="#101B30", padding=8, border_radius=12, content=ft.Column([
        ft.Text("Chat", size=12, color="#7F90B0"), bar, chat, code,
    ], spacing=6, tight=True))
    shot = ft.Screenshot(ft.Row([chat_col, ft.Container(panels, expand=True, padding=8)], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START))
    page.add(shot)

    async def grab(name: str):
        OUT.mkdir(parents=True, exist_ok=True)
        page.update()
        png = await shot.capture(pixel_ratio=1.0, delay=ft.Duration(milliseconds=250))
        (OUT / f"{name}.png").write_bytes(png)
        print(f"shot  {name}", flush=True)

    async def go():
        try:
            await asyncio.sleep(0.6)
            a, b = RESULT["a"], RESULT["b"]
            show()
            page.update()
            code.selection = ft.TextSelection(base_offset=u16(SONG, a), extent_offset=u16(SONG, b))
            code.update()
            await chat.focus()
            await asyncio.sleep(0.4)
            chat.value = QUESTION
            chat.update()
            got = code.selection
            py_sel = (py(SONG, got.base_offset), py(SONG, got.extent_offset)) if got else None
            check("UI chat focus keeps the editor range", py_sel == (a, b), repr(py_sel))
            check("UI question does not receive the slice", chat.value == QUESTION and BODY not in chat.value)
            attached = show()
            check("UI bar shows this sheet's slice", attached == (True, BODY) and bar.visible is True, repr(attached))
            snap = CS.capture_selection(state["song"], state["sheet"], code.value, (a, b))
            check("UI snapshot matches the editor", snap["text"] == BODY and code.value == SONG)
            other = "[Chorus]\nชีตสอง\n"
            state.update(song="s2", sheet="sh2", lyrics=other, sel=(other.index("ชีตสอง"), other.index("ชีตสอง") + len("ชีตสอง")), dismissed=None)
            has, text = show()
            page.update()
            msg = LC.build_user_message("Other", META, other, text, QUESTION, [], "")
            check("UI other song attaches its own slice only", has and text == "ชีตสอง" and "old line" not in msg.split("SELECTED TEXT (", 1)[1], repr(text))
            state.update(song="s1", sheet="sh1", lyrics=SONG, sel=(a, b), dismissed=None)
            show()
            status.value = "focus kept the range  |  other sheet: ชีตสอง  |  Apply keeps one Breakdown and two Choruses"
            page.update()
            await grab("07_apply")
            find(bar, "chat_sel_clear").on_click(None)
            cleared = LC.build_user_message("Test", META, SONG, "", QUESTION, [], "")
            check("UI × removes the slice from the next question", show() == (False, "") and "SELECTED TEXT (" not in cleared and cleared.endswith(QUESTION))
        except Exception:
            FAILS.append(traceback.format_exc())
            print(traceback.format_exc(), flush=True)
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "result_apply.txt").write_text("\n".join(FAILS) if FAILS else "ok", encoding="utf-8")
        await page.window.destroy()

    page.run_task(go)


if __name__ == "__main__":
    logic()
    if FAILS:
        print(f"\n{len(FAILS)} logic failure(s); the window was not opened.", flush=True)
        sys.exit(1)
    ft.run(main, port=0, view=ft.AppView.FLET_APP)
    text = (OUT / "result_apply.txt").read_text(encoding="utf-8") if (OUT / "result_apply.txt").exists() else "no result"
    if text != "ok":
        print(text, flush=True)
        sys.exit(1)
    print("ALL PASS", flush=True)
