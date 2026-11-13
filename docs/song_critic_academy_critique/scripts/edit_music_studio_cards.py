# -*- coding: utf-8 -*-
"""The Academy becomes critique-first: eight critique cards (status, short summary, full critique and actions on expanding), the lesson in a dialog, no lesson text inside the cards."""
import sys
p = sys.argv[1] if len(sys.argv) > 1 else "D:/AI/MyApps/WorkflowPad/music_studio.py"
raw = open(p, "rb").read().decode("utf-8")
crlf = "\r\n" in raw
t = raw.replace("\r\n", "\n")


def sub(old, new, count=1):
    global t
    assert t.count(old) == count, (t.count(old), old[:100])
    t = t.replace(old, new)


# 1. the five states of a card
sub('''    ASPECT_STATUS_STYLE = {"strength": ("Strength", "green"), "concern": ("Concern", "amber"), "mixed": ("Mixed / Conditional", "accent"), "no_issue": ("No Significant Issue", "green"),
                           "insufficient": ("Not Applicable / Insufficient Evidence", "text3"), "unrated": ("Not rated", "text3"), "missing": ("Not reviewed in this report", "text3")}
''', '''    GROUP_STYLE = {"good": ("Done well", "green"), "improve": ("Could be improved", "amber"), "major": ("Major issue", "red"), "na": ("Not relevant to this song", "text3"),
                   "unknown": ("Not analyzed / not enough information", "text3")}                    # the five states of a card (SR.status_group): a colour and a word, never a score
''')
sub('''    def sr_status_chip(status: str):
        label, col = ASPECT_STATUS_STYLE.get(status, ASPECT_STATUS_STYLE["missing"])
        return sr_chip(I18N.tr(label), C[col])
''', '''    def sr_status_chip(status: str, priority: str = ""):
        label, col = GROUP_STYLE[SR.status_group(status, priority)]
        return sr_chip(I18N.tr(label), C[col])
''')
# 2. Discuss from inside the dialog closes the dialog first
sub('''on_click=lambda ev, a=topic, b=t.id: ac_discuss(a, b))], spacing=6, wrap=True)]''', '''on_click=lambda ev, a=topic, b=t.id: ac_discuss_close(a, b))], spacing=6, wrap=True)]''')
sub('''on_click=lambda ev, a=topic: ac_discuss(a, ""), style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))], spacing=6, wrap=True))''', '''on_click=lambda ev, a=topic: ac_discuss_close(a, ""), style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))], spacing=6, wrap=True))''')
# 3. the lesson block and the lesson list go; the dialog comes
a = t.index("    def ac_block(topic: str):")
b = t.index("    def ac_discuss(topic: str, tid: str = \"\"):")
t = t[:a] + '''    def ac_close_dialog(_e=None):
        try:
            page.pop_dialog()
        except Exception:
            pass

    def ac_discuss_close(topic: str, tid: str = ""):
        ac_close_dialog()                                                       # the dialog is closed first, then the chat takes over
        ac_discuss(topic, tid)

    def ac_open_lesson(topic: str):
        """'Read the lesson': the lesson of one aspect in a dialog (title, principles, techniques, examples with the reason they differ, how to use it, references). Reference knowledge from the Markdown file:
        no AI request is made to open it."""
        les = AKB.load(topic, ac_lang())
        title = les.title if les.ok else I18N.tr(SR.ASPECT_TITLE.get(topic, topic))
        page.show_dialog(ft.AlertDialog(modal=False, bgcolor=C["panel"], title=ft.Row([ft.Text(title, size=15, weight=ft.FontWeight.BOLD, color=C["text1"], expand=True), ibtn(ft.Icons.CLOSE_ROUNDED, "Close", ac_close_dialog)],
                                                                                      vertical_alignment=ft.CrossAxisAlignment.CENTER),
                                        content=ft.Container(ft.Column(ac_content(topic), spacing=10, scroll=ft.ScrollMode.AUTO), width=520, height=520),
                                        actions=[ft.TextButton(I18N.tr("Close"), on_click=ac_close_dialog)]))

''' + t[b:]
# 4. the card
a = t.index("    def sr_aspect_tile(key: str,")
b = t.index("    def sr_open_aspect(key: str):")
t = t[:a] + '''    def sr_card_summary(a: "dict | None", mode: str) -> str:
        """The one or two lines of a collapsed card: the AI's own `summary` (or, when it wrote none, the start of its own observation, clipped). With no critique it says so; the program writes no critique."""
        if a is not None:
            s = (a.get("summary") or a.get("observation") or a.get("interpretation") or "").strip()
            if s and not a.get("summary") and len(s) > 150:
                s = s[:149].rstrip() + "\\u2026"
            if s:
                return s
            return I18N.tr("Nothing more was said about this aspect.")
        return {"none": I18N.tr("No critique yet: press Analyze. The lesson can be read now."), "legacy": I18N.tr("An older report has no critique for this aspect: press Analyze again."),
                "missing": I18N.tr("The Song Critic did not review this aspect in this report.")}.get(mode, "")

    def sr_aspect_card(key: str, a: "dict | None", srd: "dict | None", evid: dict, stale: bool, lyrics: str, default_open: bool = False, mode: str = "report"):
        """One of the eight aspects as a critique card. Collapsed: the number and name, the state (done well / could be improved / major issue / not relevant / not analyzed), the AI's short summary.
        Expanded: the AI's critique with its evidence, what to try and what it could cost, the proposed wording (Preview / Apply / Undo are the existing ones, only when the AI gave a real wording), and the actions:
        Read the lesson (a dialog), Discuss with the Songwriter. The lesson text is never inside the card."""
        num = SR.ASPECTS.index(key) + 1
        tkey = f"sr:aspect:{key}"

        def changed(e):
            try:
                rep_open[tkey] = bool(e.control.expanded)
            except Exception:
                pass
        status = a["status"] if a is not None else "missing"
        where = SR.refs_label([(int(x), int(y)) for x, y in sr_refs_of(a)]) if a is not None and sr_refs_of(a) else ""
        summ = sr_card_summary(a, mode if a is None else "report")
        sub_row = ft.Column([ft.Row(([ft.Text(where, size=10, color=C["text3"])] if where else []) + [sr_status_chip(status, (a or {}).get("priority", ""))], spacing=8, wrap=True, run_spacing=2,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER),
                             ft.Text(summ, size=11, color=C["text2"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)], spacing=3, tight=True)
        body: list = []
        if a is None:
            body.append(ft.Text(summ, size=11, color=C["text3"]))
        else:
            if key == "opening" and a.get("patterns"):
                body.append(ft.Row([sr_chip(I18N.tr(OPENING_LABEL[x])) for x in a["patterns"]], spacing=6, wrap=True, run_spacing=4))
                body.append(ft.Text(I18N.tr("Opening patterns are tools for reading the opening, not rules the song has to follow."), size=10, color=C["text3"], italic=True))
            if a["status"] in ("no_issue", "not_applicable", "insufficient", "unrated") and not (a.get("observation") or a.get("interpretation") or a.get("keep") or a.get("recommendation")):
                body.append(ft.Text(I18N.tr("Nothing more was said about this aspect."), size=11, color=C["text3"]))
            else:
                body.append(sr_finding(a, "song_critic", lyrics, stale, bare=True, discuss=False, preview_label="View proposed change"))
            if key == "sections" and srd is not None:
                arc = sr_critic_ribbon(srd, evid, stale)
                body += arc[1:] if arc else []
        acts = [ft.OutlinedButton(I18N.tr("Read the lesson"), icon=ft.Icons.MENU_BOOK_OUTLINED, tooltip=I18N.tr("Open the lesson on this aspect (reference knowledge, not part of the critique)"), on_click=lambda ev, k=key: ac_open_lesson(k))]
        if a is not None:
            acts.append(ft.FilledButton(I18N.tr("Discuss with Songwriter"), icon=ft.Icons.FORUM_OUTLINED, tooltip=I18N.tr("Open the Songwriter chat with this critique and its evidence; nothing changes in the lyrics"),
                                        on_click=lambda ev, i=a["id"]: sr_discuss(i), style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF")))
        body.append(ft.Row(acts, spacing=6, wrap=True, run_spacing=4))
        return ft.Container(ft.ExpansionTile(title=ft.Text(f"{num:02d} \\u00b7 {I18N.tr(SR.ASPECT_TITLE[key])}", size=13, weight=ft.FontWeight.BOLD, color=C["text1"]), subtitle=sub_row, controls=body,
                                             expanded=rep_open.get(tkey, default_open), on_change=changed, tile_padding=po(10, 2, 10, 2), controls_padding=po(10, 4, 10, 10), maintain_state=True,
                                             expanded_cross_axis_alignment=ft.CrossAxisAlignment.START),
                            border_radius=10, bgcolor=C["panel2"], border=ft.Border.all(1, C["border"]))

    def ac_cards(mode: str) -> list:
        """The eight cards of a page with no Songcraft report (none yet, an older-format report): no critique is made up, every card says so, and the lessons can be read."""
        out = [sr_title(I18N.tr("Songwriting Academy"), I18N.tr("AI critique · 8 aspects")),
               ft.Text(I18N.tr("The critique of your lyrics appears here after Analyze. The lessons are reference knowledge and can be read at any time; no AI is asked to show them."), size=11, color=C["text3"])]
        for key in SR.ASPECTS:
            out.append(sr_aspect_card(key, None, None, {}, False, lyr() if S.song else "", mode=mode))
        return out

''' + t[b:]
# 5. the Songcraft page: cards
a = t.index("    def sr_songcraft(srd: dict, evid: dict, stale: bool, lyrics: str, rep) -> list:")
b = t.index("    def sr_toggle_arc():")
t = t[:a] + '''    def sr_songcraft(srd: dict, evid: dict, stale: bool, lyrics: str, rep) -> list:
        """The Songwriting Academy: (the verdict is drawn by the caller) the eight critique cards in the fixed order; an Action Plan only when the AI wrote one."""
        out = []
        acts = srd.get("actions") or []
        got = {a["aspect"]: a for a in (srd.get("aspects") or [])}
        if acts:
            out.append(sr_card(ft.Text(I18N.tr("Start here"), size=10, weight=ft.FontWeight.W_700, color=C["accent"]), ft.Text(acts[0].get("title") or acts[0]["action"][:80], size=13, weight=ft.FontWeight.BOLD, color=C["text1"]),
                               ft.Text(acts[0]["action"], size=12, color=C["text2"], max_lines=3, overflow=ft.TextOverflow.ELLIPSIS), tint=ft.Colors.with_opacity(0.45, C["accent"])))
        first_key = acts[0].get("aspect") if acts else ""
        out.append(sr_title(I18N.tr("Songwriting Academy"), I18N.tr("AI critique \\u00b7 8 aspects")))
        for key in SR.ASPECTS:
            out.append(sr_aspect_card(key, got.get(key), srd, evid, stale, lyrics, default_open=(key == first_key and bool(first_key)), mode="missing"))
        if acts:
            out.append(sr_title(I18N.tr("Action Plan"), I18N.tr("What to consider next")))
            for i, act in enumerate(acts, 1):
                out.append(sr_action_card(act, i, stale))
        return out

''' + t[b:]
# 6. sr_finding: no own Discuss button inside a card; a label for the Preview button
sub('''    def sr_finding(f: dict, spec: str, lyrics: str, stale: bool, bare: bool = False):''', '''    def sr_finding(f: dict, spec: str, lyrics: str, stale: bool, bare: bool = False, discuss: bool = True, preview_label: str = "Preview"):''')
sub('''        if crit:
            body += sr_proposal_ui(f, sr_get("song_critic"), stale)
            body.append(ft.Row([ft.OutlinedButton(I18N.tr("Discuss with Songwriter"), icon=ft.Icons.FORUM_OUTLINED, tooltip=I18N.tr("Open the Songwriter chat with this finding and its evidence; nothing changes in the lyrics"),
                                                  on_click=lambda ev, i=f["id"]: sr_discuss(i))], spacing=6, wrap=True))''', '''        if crit:
            body += sr_proposal_ui(f, sr_get("song_critic"), stale, preview_label)
            if discuss:
                body.append(ft.Row([ft.OutlinedButton(I18N.tr("Discuss with Songwriter"), icon=ft.Icons.FORUM_OUTLINED, tooltip=I18N.tr("Open the Songwriter chat with this finding and its evidence; nothing changes in the lyrics"),
                                                      on_click=lambda ev, i=f["id"]: sr_discuss(i))], spacing=6, wrap=True))''')
sub('''    def sr_proposal_ui(f: dict, rep, stale: bool) -> list:''', '''    def sr_proposal_ui(f: dict, rep, stale: bool, preview_label: str = "Preview") -> list:''')
sub('''            out.append(ft.Row([ft.OutlinedButton(I18N.tr("Preview"), tooltip=I18N.tr("See the lines before and after; nothing changes"), on_click=lambda ev: go(False)),''',
    '''            out.append(ft.Row([ft.OutlinedButton(I18N.tr(preview_label), tooltip=I18N.tr("See the lines before and after; nothing changes"), on_click=lambda ev: go(False)),''')
# 7. the pages that have no Songcraft report
sub('''(ac_list() if spec == "song_critic" else [])''', '''(ac_cards("none") if spec == "song_critic" else [])''')
sub('''            if spec == "song_critic":
                ctl += ac_list()                                                          # no report (or an unreadable answer): the lessons can still be read''', '''            if spec == "song_critic":
                ctl += ac_cards("none")                                                   # no report (or an unreadable answer): the eight cards say so and the lessons can still be read''')
sub('''        if spec == "song_critic" and not songcraft:
            ctl += ac_list()                                                              # an older-format report has no aspect tiles: the lessons come as their own list''', '''        if spec == "song_critic" and not songcraft:
            ctl += ac_cards("legacy")                                                     # an older-format report has no critique cards: the eight cards say so and the lessons can still be read''')
open(p, "wb").write((t.replace("\n", "\r\n") if crlf else t).encode("utf-8"))
print("patched")
