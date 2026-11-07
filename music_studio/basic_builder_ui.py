"""The Basic page of the Style panel: the multi-select Music Caption Builder (Flet controls only; the logic is caption_builder.py, which has no UI).

Layout (the confirmed mock-up): Preset dropdown on top (+ Save as, Update, Rename, Delete) · cards Style / Vocal Setup / Sound & Production, each category a row of chips ·
quick presets · Additional Style Notes · ONE Caption Preview (characters, status, Copy). The Generate button is the one of the Generate page.

No LLM here, no I/O except the user's presets file. A chip click re-draws only the chips of its own category, the Preview and the preset line. Search and "more" are local to a category.
The selection lives in the song's settings (caption_builder.store), so every song and every sheet has its own, and it is saved with the project.
"""
from __future__ import annotations

from . import caption_builder as CB

FEATURED = 6                      # chips shown before "more" (a category with fewer items shows all of them)
CARD_ICONS = {"style": "MUSIC_NOTE_OUTLINED", "vocal": "MIC_NONE_OUTLINED", "sound": "TUNE_ROUNDED"}
SECTION_HINT = {"single": "Choose one", "multi": "Choose several"}


class BasicBuilder:
    def __init__(self, ft, env: dict):
        """env: C (colours), po, root (the Column that holds the page), settings() -> the open sheet's SongSettings or None, mark_dirty(), safe_update(*ctls), toast(msg, color),
        ui_card(icon, title, body), dd(label, options, value, on_select), ibtn(icon, tip, on_click, size, color, disabled, w), lang() -> 'th' | 'en', pin(ctl, text),
        show_dialog(d), pop_dialog(), copy(text), confirm(title, text, ok_label, on_ok, danger), presets_file (optional)."""
        self.ft, self.env = ft, env
        self.C = env["C"]
        self.cat = CB.load_catalog()
        self.user, self.warn = CB.load_user_presets(env.get("presets_file"))
        self.root = env["root"]
        self.expanded: dict = {}      # category -> bool  (only the screen, not saved)
        self.query: dict = {}
        self.sec: dict = {}           # category -> its controls
        self.w: dict = {}             # the other controls
        self.fixed_notes = False

    # ── small helpers ────────────────────────────────────────────────────────────────────────────────────
    def _st(self):
        return self.env["settings"]()

    def _state(self):
        st = self._st()
        return CB.effective(st, self.cat) if st is not None else ({}, "", "")

    def _lang(self) -> str:
        return self.env["lang"]()

    def _pin(self, text_ctl, text: str):
        self.env["pin"](text_ctl, text)

    def _update(self, *ctls):
        self.env["safe_update"](*[c for c in ctls if c is not None])

    # ── building ─────────────────────────────────────────────────────────────────────────────────────────
    def build(self):
        """(Re)build the page for the open song / sheet / UI language. Called when the song or the language changes, never while the user is clicking chips."""
        ft, C, po = self.ft, self.C, self.env["po"]
        self.sec, self.w = {}, {}
        if self._st() is None:
            self.root.controls = []
            self._update(self.root)
            return
        cards = []
        for key, title, sids in CB.UI_CARDS:
            rows = []
            for i, sid in enumerate(sids):
                if i:
                    rows.append(ft.Divider(height=1, color=C["border"]))
                rows.append(self._build_section(sid))
            cards.append(self.env["ui_card"](getattr(ft.Icons, CARD_ICONS[key], ft.Icons.TUNE_ROUNDED), title, rows))
        notes = ft.TextField(label="Additional Style Notes (optional)", hint_text="e.g. a story about the city at night, a warm intro, a slow fade", multiline=True, min_lines=1, max_lines=3,
                             max_length=CB.NOTES_MAX, on_change=self._on_notes, **self.env["field_style"](height=None))
        self.w["notes"] = notes
        self.w["quick"] = ft.Row(wrap=True, spacing=6, run_spacing=6)
        quick_card = self.env["ui_card"](ft.Icons.AUTO_AWESOME_OUTLINED, "Quick presets", [self.w["quick"]])
        self.root.controls = [self._build_preset_card(), *cards, quick_card, notes, self._build_preview_card()]
        self.render_all()

    def _build_preset_card(self):
        ft, C = self.ft, self.C
        dd = self.env["dd"]("Preset", [("custom", "Custom")], "custom", self._on_preset_pick)
        dd.expand = True
        dd.height = 44
        dd.label = None                                               # the card's title already says "Preset"
        add_b = self.env["ibtn"](ft.Icons.ADD_ROUNDED, "Save as a new preset", lambda e: self._ask_name("Save as a new preset", "", self._do_save_as), 18, None, False, 34)
        upd_b = self.env["ibtn"](ft.Icons.SAVE_OUTLINED, "Update this preset with the chips now", self._do_update, 18, None, False, 34)
        ren_b = self.env["ibtn"](ft.Icons.EDIT_OUTLINED, "Rename this preset", self._do_rename, 18, None, False, 34)
        del_b = self.env["ibtn"](ft.Icons.DELETE_OUTLINE_ROUNDED, "Delete this preset", self._do_delete, 18, None, False, 34)
        mod = ft.Text("", size=10, color=C["amber"], weight=ft.FontWeight.W_600)
        sub = ft.Text("Pick ready-made chips, then change them as you like", size=10, color=C["text3"])
        self.w.update(preset_dd=dd, add_b=add_b, upd_b=upd_b, ren_b=ren_b, del_b=del_b, mod=mod)
        head = ft.Row([ft.Icon(ft.Icons.LAYERS_OUTLINED, size=15, color=C["text2"]),
                       ft.Column([ft.Text("Preset", size=13, weight=ft.FontWeight.W_600, color=C["text1"]), sub], spacing=0, expand=True), mod], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        row = ft.Row([dd, add_b, upd_b, ren_b, del_b], spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        warn = ft.Text("", size=10, color=C["amber"], visible=False)
        self.w["warn"] = warn
        return ft.Container(ft.Column([head, row, warn], spacing=6, horizontal_alignment=ft.CrossAxisAlignment.STRETCH), padding=10, border_radius=10, bgcolor=C["panel2"],
                            border=ft.Border.all(1, C["accent"]))

    def _build_section(self, sid: str):
        ft, C, po = self.ft, self.C, self.env["po"]
        title = ft.Text("", size=12, weight=ft.FontWeight.W_600, color=C["text1"])
        sub = ft.Text("", size=10, color=C["text3"])
        clear = ft.Container(ft.Text("Clear", size=10, color=C["accent"]), on_click=lambda e, s=sid: self._on_clear(s), ink=True, border_radius=4, padding=po(0, 1, 4, 1), visible=False)
        chips = ft.Row(wrap=True, spacing=6, run_spacing=6)
        search = ft.TextField(hint_text="Search, or type your own phrase and press Enter", visible=False, on_change=lambda e, s=sid: self._on_search(s, e.control.value or ""),
                              on_submit=lambda e, s=sid: self._on_enter(s), **self.env["field_style"](height=34, text_size=11, content_padding=po(8, 2, 8, 2)))
        left = ft.Container(ft.Column([title, sub, clear], spacing=1, horizontal_alignment=ft.CrossAxisAlignment.START), width=104)
        right = ft.Column([chips, search], spacing=6, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self.sec[sid] = dict(title=title, sub=sub, clear=clear, chips=chips, search=search)
        return ft.Row([left, right], spacing=8, vertical_alignment=ft.CrossAxisAlignment.START)

    def _build_preview_card(self):
        ft, C, po = self.ft, self.C, self.env["po"]
        count = ft.Text("", size=10, color=C["text3"])
        status = ft.Text("", size=10, color=C["amber"])
        text = ft.Text("", size=12, color=C["text1"], selectable=True)
        copy = self.env["ibtn"](ft.Icons.CONTENT_COPY_ROUNDED, "Copy the caption", lambda e: self.env["copy"](self.w["text"].data or ""), 16, None, False, 28)
        warns = ft.Column(spacing=2, tight=True)
        self.w.update(count=count, status=status, text=text, copy=copy, warns=warns)
        head = ft.Row([ft.Icon(ft.Icons.AUTO_AWESOME_OUTLINED, size=15, color=C["text2"]),
                       ft.Text("Caption sent to ACE-Step", size=13, weight=ft.FontWeight.W_600, color=C["text1"], expand=True), count, copy], spacing=8,
                      vertical_alignment=ft.CrossAxisAlignment.CENTER)
        body = ft.Container(text, padding=8, border_radius=8, bgcolor=C["field"], border=ft.Border.all(1, C["border"]))
        return ft.Container(ft.Column([head, body, status, warns, ft.Text("Built from your chips and notes only. No AI rewrites it; what you see here is what Generate sends.", size=10, color=C["text3"])],
                                      spacing=6, horizontal_alignment=ft.CrossAxisAlignment.STRETCH), padding=10, border_radius=10, bgcolor=C["panel2"], border=ft.Border.all(1, C["border"]))

    # ── drawing ──────────────────────────────────────────────────────────────────────────────────────────
    def render_all(self):
        sel, notes, pid = self._state()
        for sid in self.sec:
            self._render_section(sid, sel, update=False)
        nt = self.w.get("notes")
        if nt is not None:
            nt.value = notes
        self._render_preset(sel, notes, pid)
        self._render_preview(sel, notes)
        self._update(self.root)

    def _chip(self, sid: str, entry: str, on: bool):
        ft, C, po = self.ft, self.C, self.env["po"]
        label = self.cat.label(entry, self._lang())
        t = ft.Text("", size=11, color="#FFFFFF" if on else C["text2"], weight=ft.FontWeight.W_600 if on else ft.FontWeight.W_400)
        self._pin(t, label)
        kids = [t] + ([ft.Icon(ft.Icons.CHECK_ROUNDED, size=12, color="#FFFFFF")] if on else [])
        tip = self.cat.phrase(entry) if self._lang() == "th" else None
        return ft.Container(ft.Row(kids, spacing=3, tight=True), padding=po(10, 4, 10, 4), border_radius=14, bgcolor=C["accent"] if on else C["field"], tooltip=tip,
                            border=ft.Border.all(1, C["accent"] if on else C["border"]), ink=True, on_click=lambda e, s=sid, en=entry: self._on_toggle(s, en))

    def _action_chip(self, label: str, on_click, accent: bool = False):
        ft, C, po = self.ft, self.C, self.env["po"]
        t = ft.Text(label, size=11, color=C["accent"])                  # a program text (+ 36 more, Show less): translated like every other UI text
        return ft.Container(t, padding=po(10, 4, 10, 4), border_radius=14, border=ft.Border.all(1, C["accent"] if accent else C["border"]), ink=True, on_click=on_click)

    def _entries_for(self, sid: str, sel: dict) -> list:
        sec, q = self.cat.sections[sid], (self.query.get(sid) or "").strip().casefold()
        picked = list(sel.get(sid, []))
        if q:
            base = [i.id for i in sec.items if q in i.en.casefold() or q in i.th.casefold()]
        elif self.expanded.get(sid):
            base = [i.id for i in sec.items]
        else:
            base = list(sec.featured) if sec.featured else [i.id for i in sec.items[:FEATURED]]
        return base + [e for e in picked if e not in base]            # what is chosen is always visible, wherever it is in the list

    def _render_section(self, sid: str, sel: dict, update: bool = True):
        ft, C = self.ft, self.C
        c, sec, lang = self.sec.get(sid), self.cat.sections[sid], self._lang()
        if c is None:
            return
        picked = set(sel.get(sid, []))
        n = len(picked)
        self._pin(c["title"], self.cat.section_label(sid, lang))
        over = CB.over_soft_limit(sel, sid, self.cat)
        if n:
            c["sub"].value = f"{n} selected" if not over else f"{n} selected (suggested up to {sec.suggested_max})"
        else:
            c["sub"].value = SECTION_HINT["single" if sec.single else "multi"]
        c["sub"].color = C["amber"] if over else C["text3"]
        c["clear"].visible = n > 0
        chips = [self._chip(sid, e, e in picked) for e in self._entries_for(sid, sel)]
        q = (self.query.get(sid) or "").strip()
        if q and not self.cat.find_phrase(q) and (CB.CUSTOM + q) not in picked:
            chips.append(self._action_chip(f'+ Add "{q}"', lambda e, s=sid: self._on_enter(s), True))
        if len(sec.items) > FEATURED:
            shown_n = len(sec.featured) if sec.featured else FEATURED
            chips.append(self._action_chip("Show less" if self.expanded.get(sid) else f"+ {len(sec.items) - shown_n} more", lambda e, s=sid: self._on_more(s)))
        c["chips"].controls = chips
        c["search"].visible = bool(self.expanded.get(sid)) and len(sec.items) > FEATURED
        if update:
            self._update(c["chips"], c["sub"], c["title"], c["clear"], c["search"])

    def _render_preset(self, sel: dict, notes: str, pid: str):
        ft, C = self.ft, self.C
        p = CB.find_preset(pid, self.user, self.cat) if pid else None
        w = self.w
        dd = w.get("preset_dd")
        if dd is None:
            return
        opts = [ft.dropdown.Option("custom", "Custom")] + [ft.dropdown.Option(x["id"], x["name"]) for x in self.cat.builtin] \
            + [ft.dropdown.Option(x["id"], "★ " + x["name"]) for x in self.user] + [ft.dropdown.Option("__manage", "Manage presets...")]
        dd.options = opts
        dd.value = p["id"] if p else "custom"
        user_p = bool(p) and not p.get("builtin")
        w["upd_b"].disabled = w["ren_b"].disabled = w["del_b"].disabled = not user_p
        mod = bool(p) and CB.is_modified(sel, notes, pid, self.user, self.cat)
        w["mod"].value = ("Modified" if mod else "") if p else ""
        quick = []
        for x in list(self.cat.builtin) + list(self.user):
            on = bool(p) and p["id"] == x["id"]
            t = ft.Text("", size=10, color="#FFFFFF" if on else C["text2"])
            self._pin(t, x["name"])
            quick.append(ft.Container(t, padding=self.env["po"](9, 4, 9, 4), border_radius=14, bgcolor=C["accent"] if on else C["field"], ink=True,
                                      border=ft.Border.all(1, C["accent"] if on else C["border"]), on_click=lambda e, i=x["id"]: self._apply_preset(i)))
        w["quick"].controls = quick
        w["warn"].value = self.warn
        w["warn"].visible = bool(self.warn)

    def _render_preview(self, sel: dict, notes: str, update: bool = False):
        w, C = self.w, self.C
        if "text" not in w:
            return
        cap = CB.compose(sel, notes, self.cat)
        n, level = CB.length_status(cap)
        w["text"].data = cap                                          # the Copy button copies exactly this
        w["text"].value = cap or "Choose chips (a Genre first) to build the caption."
        w["text"].color = C["text1"] if cap else C["text3"]
        w["copy"].visible = bool(cap)
        w["count"].value = f"{n} characters" if cap else ""
        w["status"].value = {"ok": "", "long": f"{n} characters: longer than the {CB.CAPTION_GUIDE_MAX} suggested for ACE-Step. It is sent as it is; shorter usually follows better.",
                             "too_long": f"{n} characters: over the {CB.CAPTION_HARD_MAX} limit. Generate is blocked until you remove some chips or notes (nothing is cut for you)."}[level]
        w["status"].color = C["red"] if level == "too_long" else C["amber"]
        w["status"].visible = bool(w["status"].value)
        notes_ = CB.conflicts(sel, self.cat)
        bad = CB.unresolved(sel, self.cat)
        if bad:
            notes_.append("This catalog no longer has: " + ", ".join(bad) + ". They are kept but not sent.")
        w["warns"].controls = [self.ft.Text("⚠ " + m, size=10, color=C["amber"]) for m in notes_]
        if update:
            self._update(w["text"], w["copy"], w["count"], w["status"], w["warns"])

    # ── the user's actions ───────────────────────────────────────────────────────────────────────────────
    def commit(self, sel: dict, notes: str, pid: str, touched=(), preview_only: bool = False):
        st = self._st()
        if st is None:
            return
        CB.store(st, sel, notes, pid, self.cat)
        self.env["mark_dirty"]()
        for sid in touched:
            self._render_section(sid, sel)
        self._render_preset(sel, notes, pid)
        self._render_preview(sel, notes)
        self._update(*[self.w[k] for k in ("preset_dd", "upd_b", "ren_b", "del_b", "mod", "quick", "warn", "text", "copy", "count", "status", "warns") if k in self.w])

    def _on_toggle(self, sid: str, entry: str):
        sel, notes, pid = self._state()
        self.commit(CB.toggle(sel, sid, entry, self.cat), notes, pid, [sid])

    def _on_clear(self, sid: str):
        sel, notes, pid = self._state()
        self.commit(CB.clear_section(sel, sid), notes, pid, [sid])

    def _on_more(self, sid: str):
        self.expanded[sid] = not self.expanded.get(sid)
        if not self.expanded[sid]:
            self.query[sid] = ""
            self.sec[sid]["search"].value = ""
        sel, _n, _p = self._state()
        self._render_section(sid, sel)

    def _on_search(self, sid: str, text: str):
        self.query[sid] = text
        sel, _n, _p = self._state()
        self._render_section(sid, sel)                                # only this category's chips are redrawn

    def _on_enter(self, sid: str):
        q = (self.query.get(sid) or "").strip()
        if not q:
            return
        sel, notes, pid = self._state()
        it = self.cat.find_phrase(q)
        entry = it.id if it else CB.CUSTOM + q
        if entry not in sel.get(sid, []):
            sel = CB.toggle(sel, sid, entry, self.cat)
        self.query[sid] = ""
        self.sec[sid]["search"].value = ""
        self.commit(sel, notes, pid, [sid])

    def _on_notes(self, e):
        sel, _n, pid = self._state()
        st = self._st()
        if st is None:
            return
        self.commit(sel, e.control.value or "", pid, ())

    # presets
    def _on_preset_pick(self, e):
        v = e.control.value or "custom"
        sel, notes, pid = self._state()
        if v == "__manage":
            self._render_preset(sel, notes, pid)
            self._update(self.w["preset_dd"])
            self._manage_dialog()
        elif v == "custom":
            self.commit(sel, notes, "", ())
        else:
            self._apply_preset(v)

    def _apply_preset(self, pid: str):
        p = CB.find_preset(pid, self.user, self.cat)
        if p is None:
            self.env["toast"]("That preset no longer exists.", self.C["amber"])
            return
        _s, notes, _p = self._state()
        new_notes = p.get("notes") or notes                           # a preset without notes keeps what the user wrote
        self.commit(CB.copy_sel(p["sel"]), new_notes, p["id"], list(self.sec))
        if "notes" in self.w:
            self.w["notes"].value = new_notes
            self._update(self.w["notes"])

    def _current_user_preset(self):
        _s, _n, pid = self._state()
        p = CB.find_preset(pid, self.user, self.cat) if pid else None
        return p if p and not p.get("builtin") else None

    def _save_presets(self) -> bool:
        try:
            CB.save_user_presets(self.user)
            return True
        except Exception as ex:
            self.env["toast"](f"Could not save the presets: {ex}", self.C["red"])
            return False

    def _do_save_as(self, name: str):
        sel, notes, _pid = self._state()
        try:
            p = CB.new_preset(name, sel, notes, self.user, self.cat)
        except CB.PresetError as ex:
            self.env["toast"](str(ex), self.C["amber"])
            return
        self.user.append(p)
        if self._save_presets():
            self.commit(sel, notes, p["id"], ())
            self.env["toast"](f'Preset "{p["name"]}" saved')

    def _do_update(self, e=None):
        p = self._current_user_preset()
        if not p:
            self.env["toast"]("Only your own presets can be updated. Use + to save a copy.", self.C["amber"])
            return
        sel, notes, pid = self._state()
        CB.update_preset(p, sel, notes)
        if self._save_presets():
            self.commit(sel, notes, pid, ())
            self.env["toast"](f'Preset "{p["name"]}" updated')

    def _do_rename(self, e=None, preset=None):
        p = preset or self._current_user_preset()
        if not p:
            return
        self._ask_name("Rename the preset", p["name"], lambda name, p=p: self._rename_to(p, name))

    def _rename_to(self, p: dict, name: str):
        try:
            CB.rename_preset(p, name, self.user, self.cat)
        except CB.PresetError as ex:
            self.env["toast"](str(ex), self.C["amber"])
            return
        if self._save_presets():
            sel, notes, pid = self._state()
            self._render_preset(sel, notes, pid)
            self._update(*[self.w[k] for k in ("preset_dd", "quick") if k in self.w])

    def _do_delete(self, e=None, preset=None):
        p = preset or self._current_user_preset()
        if not p:
            return
        self.env["confirm"]("Delete the preset?", f'"{p["name"]}" will be deleted. Songs that used it keep their chips.', "Delete", lambda p=p: self._delete(p), True)

    def _delete(self, p: dict):
        self.user = CB.delete_preset(p, self.user)
        sel, notes, pid = self._state()
        if self._save_presets():
            self.commit(sel, notes, "" if pid == p["id"] else pid, ())

    def _ask_name(self, title: str, value: str, on_ok):
        ft, C = self.ft, self.C
        tf = ft.TextField(value=value, autofocus=True, label="Preset name", **self.env["field_style"](height=48))

        def ok(e):
            self.env["pop_dialog"]()
            on_ok(tf.value or "")
        tf.on_submit = ok
        self.env["show_dialog"](ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Text(title), content=ft.Container(tf, width=340),
                                               actions=[ft.TextButton("Cancel", on_click=lambda e: self.env["pop_dialog"]()),
                                                        ft.FilledButton("Save", on_click=ok, style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))]))

    def _manage_dialog(self):
        ft, C = self.ft, self.C
        rows = []
        for p in self.user:
            rows.append(ft.Row([ft.Text(p["name"], size=12, color=C["text1"], expand=True),
                                ft.TextButton("Rename", on_click=lambda e, p=p: (self.env["pop_dialog"](), self._do_rename(None, p))),
                                ft.TextButton("Delete", on_click=lambda e, p=p: (self.env["pop_dialog"](), self._do_delete(None, p)))], spacing=2))
        body = ft.Column(rows or [ft.Text("You have no presets of your own yet. Choose chips, then press + to save them.", size=12, color=C["text3"])], spacing=4, tight=True)
        self.env["show_dialog"](ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Text("My presets"), content=ft.Container(body, width=380),
                                               actions=[ft.TextButton("Close", on_click=lambda e: self.env["pop_dialog"]())]))
