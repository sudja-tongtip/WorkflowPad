# -*- coding: utf-8 -*-
import sys
p = sys.argv[1]
raw = open(p, "rb").read().decode("utf-8")
crlf = "\r\n" in raw
t = raw.replace("\r\n", "\n")


def sub(old, new, count=1):
    global t
    assert t.count(old) == count, (t.count(old), old[:80])
    t = t.replace(old, new)


# 1. import
sub("from music_studio import spec_reports as SR\n", "from music_studio import spec_reports as SR\nfrom music_studio import songcraft_academy as AC       # the editable songcraft lessons under the eight aspects of the Song Critic page (no LLM)\n")

# 2. startup: lesson copies
sub("""    for note in SP.ensure_user_copies():                        # every specialist has an editable prompt copy; a copy the user edited is never overwritten
        log.info("specialist prompts: %s", note)
""", """    for note in SP.ensure_user_copies():                        # every specialist has an editable prompt copy; a copy the user edited is never overwritten
        log.info("specialist prompts: %s", note)
    try:
        for note in AC.ensure_user_copies():                    # the songcraft lessons: an editable copy of each; a copy the user edited is never overwritten
            log.info("songcraft lessons: %s", note)
    except Exception as ex:
        log.info("songcraft lessons: not installed (%s)", ex)
""")

# 3. ask_ai hook: the lesson hand-off uses the same budget, refusal and notes
sub("""                ho_text, ho_info = sr_handoff_text(ho, text, budget) if budget > 0 else (None, {"refused": True, "budget": budget, "tokens": 0})
                if ho_text is None:                                                    # the smallest useful hand-off does not fit: NOTHING is sent to the model, and the user is told why
                    TRC.log_event("handoff", specialist="songwriter", song_id=song.id, sheet_id=sheet0, finding=ho.get("finding_id") or "(whole report)", backend=backend, n_ctx=n_ctx, budget=budget, info=ho_info, text="(refused: no room)")
                    append_chat(song.id, M.ChatMessage("note", I18N.tr("The Song Critic report was NOT sent, and nothing was sent to the model: this conversation has no room for it. Start a new session (New session) and press Discuss again."),
                                                       specialist="songwriter", sheet_id=sheet0))
                    return
                user_msg = ho_text + chr(10) * 2 + user_msg
                TRC.log_event("handoff", specialist="songwriter", song_id=song.id, sheet_id=sheet0, finding=ho.get("finding_id") or "(whole report)", backend=backend, n_ctx=n_ctx, budget=budget, info=ho_info, text=ho_text)
                cut_note = (" Shortened: " + ", ".join(ho_info["shortened"]) + "." if ho_info["shortened"] else "") + (" Left out: " + ", ".join(ho_info["left_out"]) + "." if ho_info["left_out"] else "")
                append_chat(song.id, M.ChatMessage("note", f"Sent with this message: the Song Critic report ({'one finding' if ho.get('finding_id') else 'the whole report'}), about {ho_info['tokens']} tokens of a {budget}-token budget." + cut_note,
                                                   specialist="songwriter", sheet_id=sheet0))
""", """                ho_text, ho_info = sr_handoff_text(ho, text, budget) if budget > 0 else (None, {"refused": True, "budget": budget, "tokens": 0})
                is_lesson = ho.get("kind") == "lesson"
                ho_what = ("lesson:" + str(ho.get("topic")) + (":" + str(ho.get("technique")) if ho.get("technique") else "")) if is_lesson else (ho.get("finding_id") or "(whole report)")
                if ho_text is None:                                                    # the smallest useful hand-off does not fit: NOTHING is sent to the model, and the user is told why
                    TRC.log_event("handoff", specialist="songwriter", song_id=song.id, sheet_id=sheet0, finding=ho_what, backend=backend, n_ctx=n_ctx, budget=budget, info=ho_info, text="(refused: no room)")
                    append_chat(song.id, M.ChatMessage("note", I18N.tr("The songwriting lesson was NOT sent, and nothing was sent to the model: this conversation has no room for it. Start a new session (New session) and press Discuss again.") if is_lesson else
                                                       I18N.tr("The Song Critic report was NOT sent, and nothing was sent to the model: this conversation has no room for it. Start a new session (New session) and press Discuss again."),
                                                       specialist="songwriter", sheet_id=sheet0))
                    return
                user_msg = ho_text + chr(10) * 2 + user_msg
                TRC.log_event("handoff", specialist="songwriter", song_id=song.id, sheet_id=sheet0, finding=ho_what, backend=backend, n_ctx=n_ctx, budget=budget, info=ho_info, text=ho_text)
                cut_note = (" Shortened: " + ", ".join(ho_info["shortened"]) + "." if ho_info["shortened"] else "") + (" Left out: " + ", ".join(ho_info["left_out"]) + "." if ho_info["left_out"] else "")
                what_sent = ("the songwriting lesson (" + ("one technique" if ho.get("technique") else "the whole lesson") + ")") if is_lesson else f"the Song Critic report ({'one finding' if ho.get('finding_id') else 'the whole report'})"
                append_chat(song.id, M.ChatMessage("note", f"Sent with this message: {what_sent}, about {ho_info['tokens']} tokens of a {budget}-token budget." + cut_note,
                                                   specialist="songwriter", sheet_id=sheet0))
""")

# 4. sr_handoff_text: a lesson hand-off
sub('''        rep = ho["rep"]
        d = rep.data or {}
        st = S.song.settings
        state = SR.state_of(rep, LA.text_hash(lyrics_now), running=False, current_input_fp=sr_input_fp("song_critic"))''', '''        st = S.song.settings
        if ho.get("kind") == "lesson":                                             # a songwriting lesson (and one technique): the report's finding for THIS aspect travels only when it exists
            les = AC.load(ho["topic"], ac_lang())
            rep0 = ho.get("rep")
            d0 = (rep0.data or {}) if rep0 is not None and rep0.status == "ok" else {}
            srd0 = d0.get("sr") or {}
            finding, report_line, state0, why0, lyrics_then0 = None, "", "", "", lyrics_now
            if rep0 is not None and srd0.get("format") == SR.FORMAT_SONGCRAFT:
                state0 = SR.state_of(rep0, LA.text_hash(lyrics_now), running=False, current_input_fp=sr_input_fp("song_critic"))
                why0 = SR.outdated_why(rep0, LA.text_hash(lyrics_now), sr_input_fp("song_critic"))
                report_line = f"made {str(rep0.ts or '-')[:16]} · {rep0.backend or '-'} {rep0.model or ''}".strip()
                finding = next((x for x in (srd0.get("aspects") or []) if x.get("aspect") == ho["topic"]), None)
                if finding is not None:
                    lyrics_then0 = d0.get("lyrics") or lyrics_now
                else:
                    report_line += " (this report did not review this aspect; there is no AI opinion on it)"
            elif srd0:
                report_line = "an older-format Song Critic report exists for this song; it has no finding for this aspect (nothing is invented)"
            return AC.handoff_text(lesson=les, technique_id=ho.get("technique") or "", title=S.song.title or "", song_id=S.song.id, sheet_id=S.song.book.active_id,
                                   style={"genre": st.genre, "mood": st.mood, "energy": st.energy, "vocal": st.vocal, "language": st.language},
                                   settings={k: v for k, v in SH.settings_to_dict(st).items() if k in ("bpm", "timesignature", "duration", "keyscale")}, finding=finding, report_line=report_line,
                                   state=state0, why=why0, lyrics_then=lyrics_then0, lyrics_now=lyrics_now, budget_tokens=budget)
        rep = ho["rep"]
        d = rep.data or {}
        state = SR.state_of(rep, LA.text_hash(lyrics_now), running=False, current_input_fp=sr_input_fp("song_critic"))''')

# 5. the Academy UI: lesson content, the lesson block of an aspect tile, the list for a page with no Songcraft report, and Discuss
sub('''    def sr_status_chip(status: str):
        label, col = ASPECT_STATUS_STYLE.get(status, ASPECT_STATUS_STYLE["missing"])
        return sr_chip(I18N.tr(label), C[col])
''', '''    def sr_status_chip(status: str):
        label, col = ASPECT_STATUS_STYLE.get(status, ASPECT_STATUS_STYLE["missing"])
        return sr_chip(I18N.tr(label), C[col])

    # ── Songwriting Academy: the lessons of songcraft_knowledge (no LLM, no song needed). Text is read from the Markdown files; nothing of a lesson is written in this file. ──────────────────────────────────────
    def ac_lang() -> str:
        return I18N.lang() if I18N.lang() in AC.LANGS else AC.DEFAULT_LANG

    def ac_lines(text: str) -> list:
        out = []
        for ln in (text or "").split("\\n"):
            s = ln.strip()
            if s:
                out.append(ft.Text(("\\u2022 " + s[2:]) if s.startswith("- ") else s, size=12, color=C["text1"], selectable=True))
        return out

    def ac_section(label: str, text: str, color: "str | None" = None):
        return ft.Column([ft.Text(I18N.tr(label), size=11, weight=ft.FontWeight.W_700, color=color or C["accent"]), *ac_lines(text)], spacing=3, tight=True)

    def ac_technique_tile(topic: str, t):
        tkey = f"ac:{topic}:{t.id}"

        def changed(e):
            try:
                rep_open[tkey] = bool(e.control.expanded)
            except Exception:
                pass
        body = ac_lines(t.text) + [ft.Row([ft.TextButton(I18N.tr("Discuss this technique with Songwriter"), icon=ft.Icons.FORUM_OUTLINED, tooltip=I18N.tr("Open the Songwriter chat with this technique; nothing changes in the lyrics"),
                                                         on_click=lambda ev, a=topic, b=t.id: ac_discuss(a, b))], spacing=6, wrap=True)]
        return ft.Container(ft.ExpansionTile(title=ft.Text(t.title, size=12, weight=ft.FontWeight.W_600, color=C["text1"]), controls=body, expanded=rep_open.get(tkey, False), on_change=changed,
                                             tile_padding=po(8, 0, 8, 0), controls_padding=po(8, 2, 8, 8), maintain_state=True, expanded_cross_axis_alignment=ft.CrossAxisAlignment.START),
                            border_radius=8, bgcolor=C["field"], border=ft.Border.all(1, C["border"]))

    def ac_content(topic: str) -> list:
        """The lesson of one aspect as controls: what it is, why it works, how to use (with the techniques), examples, pitfalls, an exercise, the references, and the Discuss button."""
        les = AC.load(topic, ac_lang())
        if not les.ok:
            return [ft.Text(I18N.tr("This lesson is not available.") + " " + "; ".join(les.errors)[:300], size=11, color=C["amber"], selectable=True)]
        out = [ft.Text(n, size=10, color=C["amber"], selectable=True) for n in les.notices]
        out.append(ft.Text(I18N.tr("Lessons are reference knowledge, not the AI's opinion about your song. [R1] marks a principle that a cited reference supports; text with no tag is this program's own synthesis; the examples were written for teaching."),
                           size=10, color=C["text3"], italic=True))
        out.append(ac_section("What It Is", les.sections["What It Is"]))
        out.append(ac_section("Why It Works", les.sections["Why It Works"]))
        out.append(ac_section("How to Use", les.intro))
        if les.techniques:
            out.append(ft.Text(I18N.tr("Techniques"), size=11, weight=ft.FontWeight.W_700, color=C["accent"]))
            out += [ac_technique_tile(topic, t) for t in les.techniques]
        out.append(ac_section("Examples", les.sections["Examples"], C["green"]))
        out.append(ac_section("Common Pitfalls", les.sections["Common Pitfalls"], C["amber"]))
        out.append(ac_section("Try It Yourself", les.sections["Try It Yourself"]))
        refs = [ft.Text(I18N.tr("References"), size=11, weight=ft.FontWeight.W_700, color=C["accent"])]
        for r in les.references:
            refs.append(ft.Text(f"{r.id} \\u00b7 {r.title} \\u2014 {r.by}", size=11, color=C["text1"], selectable=True))
            refs.append(ft.Text(("\\u2713 " + I18N.tr("Source checked") if r.status == "verified" else I18N.tr("General knowledge, not a citation")) + ": " + r.supports + ((" \\u00b7 " + r.url) if r.url else ""), size=10, color=C["text3"], selectable=True))
        out.append(ft.Column(refs, spacing=3, tight=True))
        out.append(ft.Row([ft.FilledButton(I18N.tr("Discuss this lesson with Songwriter"), icon=ft.Icons.FORUM_OUTLINED, tooltip=I18N.tr("Open the Songwriter chat with this lesson and your current lyrics; nothing changes in the lyrics"),
                                           on_click=lambda ev, a=topic: ac_discuss(a, ""), style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))], spacing=6, wrap=True))
        out.append(ft.Text(f"{I18N.tr('Lesson version')} {les.version} \\u00b7 {les.origin}", size=9, color=C["text3"]))
        return out

    def ac_block(topic: str):
        """The lesson inside an aspect tile: collapsed by default, opened and closed on its own."""
        les = AC.load(topic, ac_lang())
        tkey = f"ac:{topic}"

        def changed(e):
            try:
                rep_open[tkey] = bool(e.control.expanded)
            except Exception:
                pass
        return ft.Container(ft.ExpansionTile(leading=ft.Icon(ft.Icons.MENU_BOOK_OUTLINED, size=16, color=C["accent"]), title=ft.Text(I18N.tr("Learn songcraft principles"), size=12, weight=ft.FontWeight.W_700, color=C["accent"]),
                                             subtitle=ft.Text(les.title if les.ok else "", size=10, color=C["text3"]), controls=ac_content(topic), expanded=rep_open.get(tkey, False), on_change=changed,
                                             tile_padding=po(8, 0, 8, 0), controls_padding=po(8, 2, 8, 10), maintain_state=True, expanded_cross_axis_alignment=ft.CrossAxisAlignment.START),
                            border_radius=8, bgcolor=ft.Colors.with_opacity(0.06, C["accent"]), border=ft.Border.all(1, ft.Colors.with_opacity(0.35, C["accent"])))

    def ac_list() -> list:
        """The eight lessons as tiles, for a Song Critic page that has no Songcraft report (none yet, or an older one): knowledge, never an analysis of the song."""
        out = [sr_title(I18N.tr("Songwriting Academy"), I18N.tr("8 lessons")),
               ft.Text(I18N.tr("Lessons on the eight aspects of a song. They are reference knowledge, not an analysis of your lyrics, and no AI was asked to show them."), size=11, color=C["text3"])]
        for i, key in enumerate(SR.ASPECTS, 1):
            les = AC.load(key, ac_lang())
            tkey = f"ac:{key}"

            def changed(e, tk=tkey):
                try:
                    rep_open[tk] = bool(e.control.expanded)
                except Exception:
                    pass
            out.append(ft.Container(ft.ExpansionTile(title=ft.Text(f"{i:02d} \\u00b7 {I18N.tr(SR.ASPECT_TITLE[key])}", size=13, weight=ft.FontWeight.BOLD, color=C["text1"]),
                                                     subtitle=ft.Row([sr_chip(I18N.tr("Lesson")), ft.Text(les.title if les.ok else "", size=10, color=C["text3"])], spacing=8, wrap=True, run_spacing=2),
                                                     controls=ac_content(key), expanded=rep_open.get(tkey, False), on_change=changed, tile_padding=po(10, 2, 10, 2), controls_padding=po(10, 4, 10, 10),
                                                     maintain_state=True, expanded_cross_axis_alignment=ft.CrossAxisAlignment.START),
                                    border_radius=10, bgcolor=C["panel2"], border=ft.Border.all(1, C["border"])))
        return out

    def ac_discuss(topic: str, tid: str = ""):
        """'Discuss this lesson with Songwriter': the Songwriter chat of this song and sheet gets a message of the user's; ONE lesson (and technique), the exercise, the Song Critic's finding for this aspect when a Songcraft report
        has one, and the current lyrics go with it as data. Same session, hard token budget and Outdated labels as the report hand-off; no Song Critic call; nothing in the lyrics changes."""
        if not S.song:
            toast("Open or create a song first.", C["amber"]); return
        if S.chat_busy_song or S.an_busy or sr["running"]:
            toast("The AI is still busy. Try again in a moment.", C["amber"]); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        les = AC.load(topic, ac_lang())
        if not les.ok:
            toast("This lesson is not available.", C["amber"]); return
        tech = les.technique(tid) if tid else None
        if tid and tech is None:
            toast("That technique is not in the lesson any more.", C["amber"]); return
        label = I18N.tr("Let's discuss this songwriting lesson") + ": " + les.title + ((" / " + tech.title) if tech else "")
        sheet0 = S.song.book.active_id
        ho_try = {"kind": "lesson", "label": label, "topic": topic, "technique": tid, "finding_id": "", "action_id": "", "rep": sr_get("song_critic"), "t": time.time()}
        room = min(3500, sr_handoff_avail(S.backend, lyr() + chr(10) * 3 + label))
        if room <= 0 or sr_handoff_text(ho_try, lyr(), room)[0] is None:                # checked BEFORE anything is sent or written to the chat
            toast("This conversation has no room for the lesson. Start a new session (New session), then press Discuss again. Nothing was sent.", C["amber"]); return
        handoff_pending[(S.song.id, sheet0)] = ho_try
        set_spec("songwriter")
        pick_ai_tab("chat")
        append_chat(S.song.id, M.ChatMessage("user", label, specialist="songwriter", sheet_id=sheet0))
        page.run_task(ask_ai, label)
''')

# 6. the lesson block inside every aspect tile of a Songcraft report
sub('''            if key == "sections":
                arc = sr_critic_ribbon(srd, evid, stale)
                body += arc[1:] if arc else []
        return ft.Container(ft.ExpansionTile(title=ft.Text(f"{num:02d} · {I18N.tr(SR.ASPECT_TITLE[key])}"''', '''            if key == "sections":
                arc = sr_critic_ribbon(srd, evid, stale)
                body += arc[1:] if arc else []
        body.append(ac_block(key))                                                # the lesson of this aspect (collapsed): shown even when the Song Critic did not review the aspect
        return ft.Container(ft.ExpansionTile(title=ft.Text(f"{num:02d} · {I18N.tr(SR.ASPECT_TITLE[key])}"''')

# 7. the Academy list where there is no Songcraft report
sub('''        if not S.song:
            sr_col.controls = [ft.Text(I18N.tr("Open or create a song first."), size=12, color=C["text3"])]
            safe_update(sr_col); return
        rep, state = sr_get(spec), sr_state(spec)''', '''        if not S.song:
            sr_col.controls = [ft.Text(I18N.tr("Open or create a song first."), size=12, color=C["text3"])] + (ac_list() if spec == "song_critic" else [])
            safe_update(sr_col); return
        rep, state = sr_get(spec), sr_state(spec)''')
sub('''            elif spec == "vocal_flow" and S.song and LA.sung_lines(lyr()):
                try:
                    _t, ev_now = SR.evidence_vocal(lyr(), song_lang(), SH.settings_to_dict(S.song.settings), li_corrections())
                    ctl += sr_vocal_evidence(ev_now, False)
                except Exception as ex:
                    dbg_once("sr vocal preview", ex)
            sr_col.controls = ctl''', '''            elif spec == "vocal_flow" and S.song and LA.sung_lines(lyr()):
                try:
                    _t, ev_now = SR.evidence_vocal(lyr(), song_lang(), SH.settings_to_dict(S.song.settings), li_corrections())
                    ctl += sr_vocal_evidence(ev_now, False)
                except Exception as ex:
                    dbg_once("sr vocal preview", ex)
            if spec == "song_critic":
                ctl += ac_list()                                                          # no report (or an unreadable answer): the lessons can still be read
            sr_col.controls = ctl''')
sub('''        ctl.append(sr_diag_tile(spec, rep, state))
        sr_col.controls = ctl
        safe_update(sr_col)

    def sr_start(spec: str):''', '''        if spec == "song_critic" and not songcraft:
            ctl += ac_list()                                                              # an older-format report has no aspect tiles: the lessons come as their own list
        ctl.append(sr_diag_tile(spec, rep, state))
        sr_col.controls = ctl
        safe_update(sr_col)

    def sr_start(spec: str):''')

for a, b in (("from music_studio import songcraft_academy as AC ", "from music_studio import songcraft_academy as AKB "), ("AC.ensure_user_copies", "AKB.ensure_user_copies"), ("AC.load(", "AKB.load("), ("AC.LANGS", "AKB.LANGS"),
             ("AC.DEFAULT_LANG", "AKB.DEFAULT_LANG"), ("AC.handoff_text", "AKB.handoff_text")):
    t = t.replace(a, b)                                            # `AC` is the lyric_actions module in this file: the academy is AKB
open(p, "wb").write((t.replace("\n", "\r\n") if crlf else t).encode("utf-8"))
print("patched", len(t))
