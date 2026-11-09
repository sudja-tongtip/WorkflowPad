"""
WorkflowPad Music Studio: a music library and a lyrics editor for ACE-Step 1.5 (through your own ComfyUI).
Two pages: My Music (library + player) and Editor (lyrics, AI chat, settings, takes). The logic lives in music_studio/ (model, store, compiler,
lyrics_tools, comfy_audio, player, llm_chat); this file is the Flet screen only.
"""
import asyncio, copy, json, logging, logging.handlers, os, re, subprocess, sys, tempfile, threading, time, types, zlib
from pathlib import Path
from dataclasses import asdict, replace
import flet as ft
from flet_code_editor import CodeEditor, CodeLanguage, GutterStyle
import app_error as _app_error
import update_check as _update_check

from music_studio import model as M
from music_studio import store as ST
from music_studio import compiler as K
from music_studio import lyrics_tools as T
from music_studio import tag_parser as TP
from music_studio import section_cues as SC
from music_studio import cursor_context as CC
from music_studio import section_preview as PV
from music_studio.audio_player import MciPlayer
from music_studio.wmp_player import WmpPlayer
from music_studio import cue_library as CL
from music_studio import lyric_analysis as LA
from music_studio import lyric_rhyme as LR
from music_studio import rhyme_v6 as R6                  # the Rhyme Analyzer's contract (not RV: that name is lyric_review below)
from music_studio import live_scheduler as LS
from music_studio import vocal_cues as VC
from music_studio import rhyme_flow as RF
from music_studio import python_rhyme_evidence as PRE
from music_studio import live_dead as LD
from music_studio import report_steps as RPT              # the Songwriter / Music Producer report asked step by step (summary, one section at a time, suggestions)
from music_studio import rhyme_steps as RS               # the Rhyme Analyzer asked step by step (overview, then one section at a time, shown as it arrives)
from music_studio import rhyme_color as RC
from music_studio import lyric_review as RV
from music_studio import specialists as SP
from music_studio import specialist_engine as SE
from music_studio import specialist_trace as TRC
from music_studio.songwriting_craft import SONGWRITING_CRAFT
from music_studio import take_feedback as TF
from music_studio import lyric_rhythm as RT
from music_studio import lyric_thesaurus as TH
from music_studio import lyric_actions as AC
from music_studio import presets as PS
from music_studio import timeline as TL
from music_studio import sheets as SH
from music_studio import take_snapshot as TS
from music_studio import template_arrange as TA
from music_studio import ai_music_director as AD
from music_studio import basic_caption as BC
from music_studio import caption_builder as CB
from music_studio import basic_builder_ui as BBU
from music_studio import live_insights as LI
from music_studio import songwriter_tools as SWT
from music_studio import ace_step_knowledge as AK
from music_studio import comfy_audio as CA
from music_studio import quality_presets as QP
from music_studio import llm_chat as LC
from music_studio import chat_selection as CS
from music_studio import chat_tags
from music_studio import clear_tags as CG
from music_studio import i18n as I18N
from music_studio import font_scale as FSC
from music_studio import log_view as LV
from music_studio import feature_flags as FF
from music_studio import generation as G
from music_studio import kaola_jobs as KJ
from music_studio import waveform as WF
from music_studio import video_export as VX
from music_studio import repaint_drafts as RD
from music_studio import editor_sync as ES
from music_studio import editor_menu as EM
from music_studio import lyric_formatter as LF
from music_studio.player import PlayerService, QItem

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

APP_ID = "music_studio"
APP_NAME = "WorkflowPad - Music Studio"
APP_VERSION = "0.3.4"
APP_ROOT = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
AUDIO_API_DIR = APP_ROOT / "comfy_apis" / "audio"
LLM_DIR = APP_ROOT / "comfy_apis" / "llm"
CFG_FILE = Path(os.environ.get("APPDATA", str(Path.home()))) / "WorkflowPad" / "music_studio.json"

C = {"bg": "#0A1220", "top": "#0D1729", "panel": "#101B30", "panel2": "#16233D", "field": "#0C1628", "border": "#223352", "accent": "#2B73FF",
     "accent_soft": "#173766", "text1": "#E7EEFA", "text2": "#98A8C4", "text3": "#7F90B0", "green": "#2FBF71", "red": "#F0616D", "amber": "#F5B83D",
     "paper": "#F6F8FC", "ink": "#1A2233", "paper_dim": "#9AA6BA"}
COVER_COLORS = [("#F59E0B", "#7C2D12"), ("#3B82F6", "#0B1F4D"), ("#10B981", "#064E3B"), ("#A855F7", "#2E1065"), ("#EF4444", "#450A0A"), ("#06B6D4", "#083344"),
                ("#EC4899", "#500724"), ("#84CC16", "#1A2E05")]
LANGS = [("th", "Thai"), ("en", "English"), ("zh", "Chinese"), ("ja", "Japanese"), ("ko", "Korean"), ("es", "Spanish"), ("fr", "French"), ("de", "German"),
         ("it", "Italian"), ("pt", "Portuguese"), ("ru", "Russian"), ("vi", "Vietnamese"), ("id", "Indonesian"), ("hi", "Hindi"), ("ar", "Arabic"),
         ("tr", "Turkish"), ("nl", "Dutch"), ("pl", "Polish"), ("uk", "Ukrainian")]
from music_studio.music_options import KEYS as MO_KEYS, KEYS, GENRES, MOODS, VOCALS, ENERGIES, TIMESIGS, BPM_RANGE, DURATION_RANGE     # the lists the controls offer (also used to check AI suggestions)


def po(left=0, top=0, right=0, bottom=0):
    return ft.Padding(left=left, top=top, right=right, bottom=bottom)


def br(r):
    return ft.BorderRadius(r, r, r, r)


def _load_cfg() -> dict:
    try:
        return json.loads(CFG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cfg(d: dict) -> None:
    try:
        CFG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CFG_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


class ScrollTracker:
    """Tells the user's scrolling from the program's. The user's wheel / drag always sends a `user` notification (direction forward / reverse) before its start / update / end events
    (seen in the Flet event stream); scroll_to() and the framework's own caret reveal do not. Only user scrolling changes `user_pos`, and `user_seq` counts it, so a restore that
    is on its way can see that the user scrolled meanwhile and give way. No time window is involved."""

    def __init__(self):
        self.pos = 0.0               # where the editor is now, whoever moved it
        self.user_pos = 0.0          # where the user left it
        self.user_active = False
        self.user_seq = 0
        self.armed = None            # the position to keep after a push, until the user does something
        self.fixes_left = 0

    def reset(self) -> None:
        self.__init__()

    def arm(self, pos: float, fixes: int = 2) -> None:
        self.armed, self.fixes_left = float(pos), fixes

    def disarm(self) -> None:
        self.armed = None

    def on_event(self, kind: str, direction: str | None, pixels: float) -> float | None:
        """kind: start / update / end / user / overscroll; direction: forward / reverse / idle (for `user`). Returns a position to scroll back to when the framework (not the user)
        has moved an armed editor away from it (checked when the movement has ended, and at most `fixes_left` times per push), else None."""
        self.pos = float(pixels)
        if kind == "user":
            if direction in ("forward", "reverse"):
                self.user_active = True
                self.user_seq += 1
                self.user_pos = float(pixels)
                self.armed = None                                   # the user is scrolling: nothing may pull the editor back
            return None
        if self.user_active:
            self.user_pos = float(pixels)
            self.user_seq += 1
            if kind == "end":
                self.user_active = False
            return None
        if self.armed is not None and kind == "end" and self.fixes_left > 0 and abs(self.pos - self.armed) > 1.0:
            self.fixes_left -= 1
            return self.armed
        return None

    def user_moved_since(self, seq0: int) -> bool:
        return self.user_seq != seq0 or self.user_active

    def anchor(self) -> float:
        """The position a push has to keep: where the editor really is (so scrolling the user caused by the keyboard or by typing counts too, not only the wheel), or, right
        after a push, the position that push armed (a jump of the framework that has not been undone yet must not become the new anchor)."""
        return self.armed if self.armed is not None else self.pos


class EditHistory:
    """Undo / redo of the lyrics text. record() is called BEFORE a change with the text and selection that are about to be replaced."""
    LIMIT = 300
    BURST_SECONDS = 1.2

    def __init__(self):
        self.undo: list = []
        self.redo: list = []
        self.last_burst = 0.0

    def clear(self) -> None:
        self.undo.clear(); self.redo.clear(); self.last_burst = 0.0

    def record(self, text: str, sel: tuple, burst: bool, now: float | None = None) -> bool:
        """True when a new undo step was added. Typing keys in one go (burst) is one step. ANY new edit makes the redo history invalid, inside a burst too."""
        now = time.time() if now is None else now
        self.redo.clear()
        if burst and self.undo and now - self.last_burst < self.BURST_SECONDS:
            self.last_burst = now
            return False
        self.undo.append((text, sel))
        del self.undo[:-self.LIMIT]
        self.last_burst = now if burst else 0.0
        return True

    def step_back(self, cur_text: str, cur_sel: tuple):
        """(text, selection) to go back to, or None. The current state becomes the redo step; the next typing starts a new undo step."""
        if not self.undo:
            return None
        self.redo.append((cur_text, cur_sel))
        self.last_burst = 0.0
        return self.undo.pop()

    def step_forward(self, cur_text: str, cur_sel: tuple):
        if not self.redo:
            return None
        self.undo.append((cur_text, cur_sel))
        self.last_burst = 0.0
        return self.redo.pop()


log = logging.getLogger("music_studio.editor")


def setup_logging() -> None:
    """Quiet by default: warnings and errors go to %LOCALAPPDATA%\\WorkflowPad\\logs\\music_studio.log. MS_DEBUG=1 also prints the editor's step-by-step debug lines to the console."""
    if log.handlers:
        return
    log.setLevel(logging.DEBUG)
    try:
        d = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WorkflowPad" / "logs"
        d.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(d / "music_studio.log", maxBytes=400_000, backupCount=2, encoding="utf-8")
        fh.setLevel(logging.WARNING)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(fh)
    except OSError:
        pass
    if os.environ.get("MS_DEBUG"):
        sh = logging.StreamHandler()
        sh.setLevel(logging.DEBUG)
        sh.setFormatter(logging.Formatter("[editor %(relativeCreated)d ms] %(message)s"))
        log.addHandler(sh)


def classify_selection(ev: tuple, arrival_pending: bool, arrival_at: int, echoes: list) -> str:
    """Who caused a selection event of the lyrics field? (ev, arrival_at and echoes are UTF-16 offsets)
    'arrival'    : the field reporting that a text we pushed arrived (its caret is at the end of that text)
    'echo'       : the field confirming the selection we sent last
    'stale_echo' : the confirmation of an older selection we sent (a newer one has been sent since)
    'user'       : anything else. No time window decides this, only what we have sent and not yet seen confirmed.
    Limit that Flet cannot remove: the event carries no origin, so a user click on EXACTLY the offset of an unconfirmed arrival / stale echo is taken for that echo (once)."""
    if arrival_pending and ev == (arrival_at, arrival_at):
        return "arrival"
    if echoes:
        if ev == echoes[-1]:
            return "echo"
        if ev in echoes[:-1]:
            return "stale_echo"
    return "user"


class SelectionBook:
    """What the lyrics field is known to show, and which of its selection events are echoes of ours.

    sent       : the last text we pushed (NOT proof that the client has it: update() only means the request left)
    confirmed  : the last text the client has PROVEN to show: the field reported its arrival (caret at the end of the pushed text), confirmed a selection we sent after
                 it, or the user typed it. Events arrive in the order the client produced them, so an event that comes BEFORE the arrival report refers to the old text.
    Echo lifecycle: an expected echo lives until it is seen, until the user acts (any user event, typing), until the song changes or until three newer ones push it out."""

    def __init__(self):
        self.reset()

    def reset(self) -> None:
        self.confirmed = None
        self.sent = None
        self.echoes: list = []
        self.echo_py: dict = {}
        self.stale: list = []            # offsets of echoes the user has already replaced; the next report of one is that echo, once
        self.arrival_at = -1
        self.arrival_pending = False

    def push_text(self, text: str) -> None:
        self.sent = text
        self.arrival_at = idx16(text, len(text))
        self.arrival_pending = True
        self.stale = []                                      # the offsets belonged to the previous text

    def push_selection(self, text: str, a: int, b: int) -> tuple:
        ev = (idx16(text, a), idx16(text, b))
        self.echoes = (self.echoes + [ev])[-3:]
        self.echo_py[ev] = (a, b)
        return ev

    def typed(self, text: str) -> None:
        self.confirmed = self.sent = text
        self.echoes.clear(); self.echo_py.clear()
        self.arrival_pending = False

    def client_text(self, ev_end16: int, current: str) -> str:
        """The text the offsets of an event refer to."""
        c, sn = self.confirmed, self.sent
        if sn is None or sn == c:
            return c if c is not None else current
        if c is None:
            return sn
        return sn if ev_end16 > idx16(c, len(c)) else c      # offsets beyond the old text can only belong to the new one

    def note_arrival(self) -> None:
        self.confirmed, self.arrival_pending = self.sent, False

    def take_echo(self, ev: tuple):
        """The Python selection we sent for this echo, or None when we have no record of it. The echo is then forgotten."""
        self.confirmed, self.arrival_pending = self.sent, False   # a selection applied after the text can only come from a field that has the text
        sel = self.echo_py.get(ev)
        self.echoes.clear(); self.echo_py.clear()
        return sel

    def adopt_user(self, ev: tuple, current: str) -> tuple[int, int]:
        """A selection the user made. Echoes still waiting become stale: one later report of those offsets is that echo, not a new click."""
        self.stale = (list(self.stale) + list(self.echoes))[-6:]
        self.echoes.clear(); self.echo_py.clear()
        self.arrival_pending = False
        client = self.client_text(ev[1], current)
        a, b = idx_from16(client, ev[0]), idx_from16(client, ev[1])
        if client != current:                                       # the song's text is newer than the one the client shows: carry the position over
            a, b = map_index(client, current, a), map_index(client, current, b)
        return (a, b)

    def event(self, ev: tuple, current: str):
        """(kind, selection) for a selection event; selection = the new Python-index selection to adopt (None: leave S.sel alone)."""
        kind = classify_selection(ev, self.arrival_pending, self.arrival_at, self.echoes)
        if kind == "arrival":
            self.note_arrival()
            return kind, None
        if kind == "stale_echo":
            return kind, None
        if kind == "echo":
            return kind, self.take_echo(ev)
        return kind, self.adopt_user(ev, current)


def restore_allowed(irev0: int, irev_now: int, tracker, user_seq0: int, restoring: bool, still_current: bool) -> str | None:
    """None when a scroll restore may be sent; otherwise the reason it must be dropped. Checked right before every restore."""
    if not still_current:
        return "the song or the request changed"
    if irev_now != irev0:
        return "the user clicked, typed or scrolled since the push began"
    if tracker.user_moved_since(user_seq0):
        return "the user is scrolling"
    if restoring:
        return "a restore is already on its way"
    return None


def map_index(old: str, new: str, i: int) -> int:
    """Where position i of `old` is in `new`, when `new` is `old` with one edit (the common prefix and suffix are kept). Inside the edited part it lands at the start of the edit."""
    n = min(len(old), len(new))
    p = 0
    while p < n and old[p] == new[p]:
        p += 1
    q = 0
    while q < n - p and old[len(old) - 1 - q] == new[len(new) - 1 - q]:
        q += 1
    if i <= p:
        return i
    if i >= len(old) - q:
        return max(0, min(len(new), i + len(new) - len(old)))
    return p


def idx16(text: str, idx: int) -> int:
    """Python index (code points) -> Flet / Dart offset (UTF-16 code units). Thai is one unit per letter; an emoji is two."""
    return sum(2 if ord(ch) > 0xFFFF else 1 for ch in text[:idx])


def idx_from16(text: str, off16: int) -> int:
    """Flet / Dart offset (UTF-16 code units) -> Python index. An offset inside a surrogate pair rounds up to after that character."""
    n = 0
    for i, ch in enumerate(text):
        if n >= off16:
            return i
        n += 2 if ord(ch) > 0xFFFF else 1
    return len(text)


TEMPLATE_EXTRAS_VISIBLE = False  # the Templates sub-tabs "Custom Tools" and "AI Prompts" (and the New / Manage Tools buttons) are hidden for now; their code and saved presets are untouched. True shows them again.
PREVIEW_VISIBLE = False         # the Workspace "Preview" tab (section audio preview) is hidden for now; its code, the saved previews and the preview engine are untouched. Set True to show it again.
TIMELINE_VISIBLE = False        # the Workspace "Timeline" tab is hidden for now; its code and each song's timeline.json are untouched. Set True to show it again.
MISSING_TXT = "Not saved"


def fmt_dur(sec: float) -> str:
    s = max(0, int(round(sec)))
    return f"{s // 60}:{s % 60:02d}"


def short_date(ts: str) -> str:
    return ts[:16] if ts else ""


def main(page: ft.Page):
    setup_logging()
    TRC.ENABLED = bool(_load_cfg().get("spec_trace", True)) if "_load_cfg" in globals() else True
    for note in SP.ensure_user_copies():                        # every specialist has an editable prompt copy; a copy the user edited is never overwritten
        log.info("specialist prompts: %s", note)
    page.title = f"{APP_NAME} {APP_VERSION}"
    page.bgcolor = C["bg"]
    page.padding = 0
    page.theme_mode = ft.ThemeMode.DARK
    page.window.width, page.window.height = 1500, 920
    page.window.min_width, page.window.min_height = 1100, 700
    _ico = APP_ROOT / "icons" / "music_studio.ico"
    if _ico.exists():
        page.window.icon = str(_ico)

    cfg = _load_cfg()
    LC.set_context_overrides(cfg.get("ctx_local"), cfg.get("ctx_grok"))        # the context sizes typed in Settings (empty = the default of the flow)
    I18N.install(ft)                                        # every text a control shows is translated (music_studio/locales); Thai is the default
    I18N.set_lang(cfg.get("lang", I18N.DEFAULT_LANG))
    FSC.install(ft)                                         # the text size chosen in Settings (cfg "font_scale", percent; missing = 100 = the sizes written in the code)
    FSC.set_initial(cfg.get("font_scale"))
    page.theme = page.dark_theme = FSC.theme(ft)             # None at 100%: the default theme is kept
    player = PlayerService()
    clip = ft.Clipboard()
    page.services.append(clip)

    def cfg_num(key: str, default: float, lo: float, hi: float) -> float:
        """A layout number from the config: a damaged value (text, null, NaN) gives the default, anything else is kept inside its limits."""
        try:
            v = float(cfg.get(key, default))
        except (TypeError, ValueError):
            return float(default)
        return max(float(lo), min(float(hi), v)) if v == v else float(default)

    # ── state ────────────────────────────────────────────────────────────────────────────────────────────
    class S:
        tab = "library"                         # the page shown: "library" (My Music) or "editor" (the active song tab)
        lib: list = []                          # ST.list_songs()
        song: M.Song | None = None              # the song open in the editor (active song)
        dirty = False
        hist = EditHistory()
        tools_user = cfg.get("tools_open")      # None: the toolbox is shown when there is room; True / False: the user's choice (kept in the config)
        sel = (0, 0)                            # selection in the active song's lyrics (python indexes)
        chat: list = []
        chat_busy_song = ""
        takes: list = []
        runs: list = []
        host = cfg.get("host", "127.0.0.1:8188")
        conn = False
        gen: dict | None = None                 # {song_id, status, pid, cancel}
        search = ""
        show_old = False                        # the chat shows the messages of earlier runs of the program (they are kept in the chat file; the chat starts fresh at every start)
        sort = "new"
        lib_at = 0.0                            # when S.lib was read from disk (time.time())
        tracks_all = False                      # the Song List shows every take, not only the first TRACK_LIMIT
        source = {"type": "all", "id": None}    # what the Song List shows: all | favorites | project (id = song id). NOT the queue that is playing
        queue_src = ""                          # name of the source of the queue that is playing now
        ai_w = cfg_num("ai_w", 270, 240, 360)                 # AI Assistant column
        side_w = cfg_num("side_w", 360, 320, 560)             # Collapsible Right Sidebar, expanded
        side_open = bool(cfg.get("side_open", True))          # the USER's choice; the window may collapse it temporarily (see apply_widths)
        spec = SP.UPGRADED.get(SP.canon(cfg.get("spec") or ""), SP.canon(cfg.get("spec") or "")) if SP.UPGRADED.get(SP.canon(cfg.get("spec") or ""), SP.canon(cfg.get("spec") or "")) in SP.CHAT_IDS else SP.DEFAULT_CHAT      # the specialist the chat is talking to
        ws_open = False                                                        # the Workspace is gone: its tools live in the Right Panel tabs
        ws_tab = cfg.get("ws_tab") if cfg.get("ws_tab") in (("lyrics", "actions", "tools") + (("preview",) if PREVIEW_VISIBLE else ()) + (("timeline",) if TIMELINE_VISIBLE else ())) else "actions"     # an unknown / damaged value (or the old "templates" tab, now a dialog) falls back to AI Actions
        ws_h = cfg_num("ws_h", 170, 100, 420)
        ai_tab = "chat"
        an_busy = ""                            # song id of the Lyrics Analysis that is waiting for the AI ("" = none)
        an_progress = ""
        an_kind = ""                            # which analysis is waiting for the AI: "syl" (syllables) or "rhyme"; only one runs at a time
        backend = cfg.get("chat_backend", "")

    reg_output: list = []                       # (take_id, IconButton) play buttons, per panel
    reg_libtakes: list = []
    reg_libsongs: list = []                     # (song_id, take_id, IconButton)
    player_views: list = []                     # callables that refresh a player bar

    class Sess:
        """Everything one open song tab owns: the draft (the Song object itself), its history, caret, scroll position and chat. S.* mirrors the ACTIVE session;
        the editor view is one set of controls that shows one session at a time."""
        def __init__(self, song: M.Song, dirty: bool = False):
            self.song, self.dirty = song, dirty
            SH.ensure_book(song)
            self.rt = {}                        # sheet id -> the runtime state of a sheet that is NOT being edited now (history, caret, scroll, analysis); never saved to disk
            self.dirty_sheets = set()           # sheets changed since the last save (the dot on their tab)
            self.hist = EditHistory()
            self.sel = (0, 0)
            self.chat = ST.load_chat(song.id)
            self.scroll = 0.0
            self.analysis = None                # Lyrics Analysis of THIS song: {'snap', 'result', 'at', 'last_error'?}; never shared between songs
            self.rhyme = None                   # Rhyme analysis of THIS song (same shape as `analysis`, with an LR.RhymeResult); independent of the syllable result and its corrections
            self.rhythm = None                  # Rhythm / singability analysis of THIS song (same shape as `rhyme`, with an RT.RhythmResult made from the effective syllable counts)
            self.thes = None                    # Thesaurus result of THIS song (the latest one; same shape as `rhythm`, with a TH.ThesResult)
            self.thes_sel = None                # the user's last selection in THIS song's lyrics, captured when they made it: {start, end, text} (Python offsets); None = nothing selected
            self.review = None                  # the AI Review of the active sheet: {'result': RV.ReviewResult} | {'busy': True, 'scope'} | {'error', 'scope'}; None = no review
            self.review_dec = []                # the user's decisions in the reviews of this sheet: [(applied | ignored, original, suggestion)], so a new review does not repeat itself
            self.report_on = False              # the Song Analysis Report is shown for this sheet (Analyze Song was pressed)
            self.specreports = {}               # specialist id -> SpecReport of the active sheet (the latest; loaded from the session files, not asked for again)
            self.fd_status = {}                 # finding id -> applied | ignored (this sheet, this report)
            self.fd_undone = {}                 # finding id -> the 'applied:<hash>' it had before Undo took it back (Redo to exactly those lyrics marks it applied again)
            self.report_hash = ""               # hash of the lyrics the report was started for
            self.an_corr = {}             # the user's manual syllable corrections of THIS song: {line id: LA.Correction} (saved in analysis.json)
    sessions: dict = {}                         # song id -> Sess (one per open Editor Tab; the song id is the tab key)
    open_tabs: list = []                        # song ids in the order the tabs were opened
    COLLECTIONS = {"all": ("All Songs", ft.Icons.LIBRARY_MUSIC_ROUNDED), "favorites": ("Favorites", ft.Icons.STAR_ROUNDED)}

    # ── generic helpers ──────────────────────────────────────────────────────────────────────────────────
    def toast(msg: str, color: str | None = None):
        page.show_dialog(ft.SnackBar(ft.Text(msg, color="#FFFFFF"), bgcolor=color or "#243653", duration=3500))

    _pop_original = page.pop_dialog

    def _pop_dialog():
        """Close the topmost open dialog that is NOT a toast. (Flet keeps toasts and dialogs in one stack and pops the newest: after a toast, a click on Cancel / Apply closed the toast and left the dialog open.)"""
        try:
            for dlg in reversed(page._dialogs.controls):
                if dlg.open and not isinstance(dlg, ft.SnackBar):
                    dlg.open = False
                    dlg.update()
                    return dlg
        except Exception as ex:
            log.warning("pop_dialog: the dialog stack could not be read (%r); the default pop is used", ex)
        return _pop_original()
    try:
        page.pop_dialog = _pop_dialog
    except Exception as ex:
        log.warning("pop_dialog could not be replaced (%r): a toast may be closed instead of a dialog", ex)

    RUN_START = M.now_str()                                       # chat messages older than this belong to an earlier run: a new run starts a new chat session (nothing is deleted)
    _dbg_seen: set = set()
    PERF = bool(os.environ.get("MS_PERF"))
    _perf_file = {"f": None}

    def _perf_write(label: str, ms: float) -> None:
        """One line of the perf log (MS_PERF=1 only; a no-op otherwise). Used by _perf and by the counters (cache hits, drag events, autosave times)."""
        if not PERF:
            return
        try:
            if _perf_file["f"] is None:
                d = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "WorkflowPad" / "logs"
                d.mkdir(parents=True, exist_ok=True)
                _perf_file["f"] = open(d / "music_studio_perf.log", "a", encoding="utf-8", buffering=1)
            _perf_file["f"].write(f"{time.strftime('%H:%M:%S')} {label} {ms:.1f}\n")
        except OSError:
            pass

    def _perf(fn, name: str = ""):
        """MS_PERF=1: every call of `fn` is timed (milliseconds, on the thread that runs the UI) and appended to music_studio_perf.log. Off by default: `fn` is returned as it is."""
        if not PERF:
            return fn
        label = name or fn.__name__

        def timed(*a, **k):
            t0 = time.perf_counter()
            try:
                return fn(*a, **k)
            finally:
                _perf_write(label, (time.perf_counter() - t0) * 1000)
        timed.__name__ = label
        return timed

    _dbg_count: dict = {}

    def dbg_once(where: str, ex: Exception, warn: bool = False) -> None:
        """A swallowed error goes to the log ONCE per (place, error kind, message): timers and repeated redraws can never flood the log (at most 300 different ones; the repeat counts are written at 10, 100 and 1000),
        and nothing is shown to the user or raised. warn=True (UI updates): a WARNING in the log file, so it can be found without MS_DEBUG."""
        key = (where, type(ex).__name__, re.sub(r"\d+", "#", str(ex))[:80])          # control ids / counters in the text must not make every repeat look new
        n = _dbg_count[key] = _dbg_count.get(key, 0) + 1
        if key in _dbg_seen:
            if n in (10, 100, 1000):
                (log.warning if warn else log.debug)("ignored error in %s repeated %d times: %s: %s", where, n, type(ex).__name__, ex)
            return
        if len(_dbg_seen) >= 300:
            return
        _dbg_seen.add(key)
        (log.warning if warn else log.debug)("ignored error in %s: %s: %s", where, type(ex).__name__, ex)

    def _ctl_name(c) -> str:
        """Which control a failed update was for: its class and the first thing that identifies it (label, hint, text, key), and whether it sits in a dialog."""
        tag = next((str(getattr(c, a))[:24] for a in ("label", "hint_text", "key", "tooltip", "value", "text", "title") if isinstance(getattr(c, a, None), (str, int)) and getattr(c, a, "") != ""), "")
        return type(c).__name__ + (f"[{tag}]" if tag else "") + (" (dialog open)" if getattr(page, "dialog", None) else "")

    def safe_update(*ctls):
        for c in ctls:
            try:
                c.update()
            except Exception as ex:
                dbg_once("update " + _ctl_name(c), ex, warn=True)

    def ibtn(icon, tip, on_click, size=18, color=None, disabled=False, w=34):
        return ft.IconButton(icon, icon_size=size, tooltip=tip, on_click=on_click, icon_color=color or C["text2"], width=w, height=w, padding=0, disabled=disabled)

    # Editor toolbar buttons: icon only, one size, rounded. Blue = asks the AI, grey = tools and view. The hover wash is the only text-free feedback besides the tooltip.
    TB_AI, TB_AI_BG, TB_AI_HOVER, TB_HOVER, TB_GROUP = "#7FA6FF", "#292B73FF", "#472B73FF", "#14FFFFFF", "#0AFFFFFF"

    def tb_style(hover, base="#00000000"):
        return ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10), padding=0, bgcolor={ft.ControlState.HOVERED: hover, ft.ControlState.DEFAULT: base})

    def tbtn(icon, tip, on_click, ai=False, disabled=False):
        return ft.IconButton(icon, icon_size=20, tooltip=tip, on_click=on_click, icon_color=TB_AI if ai else C["text2"], width=36, height=36, padding=0, disabled=disabled,
                             style=tb_style(TB_AI_HOVER if ai else TB_HOVER))

    def tpop(icon, tip, items, ai=False, caret=False):
        """A menu button that looks like tbtn; `caret` adds the small arrow that says "a menu opens"."""
        col, hov = (TB_AI if ai else C["text2"]), (TB_AI_HOVER if ai else TB_HOVER)
        kids = [ft.Container(ft.Icon(icon, size=20, color=col), alignment=ft.Alignment(0, 0), width=36, height=36)]
        if caret:
            kids.append(ft.Container(ft.Icon(ft.Icons.ARROW_DROP_DOWN_ROUNDED, size=14, color=col), right=1, bottom=1))
        box = ft.Container(ft.Stack(kids, width=36, height=36), width=36, height=36, border_radius=10)

        def hover(e):
            box.bgcolor = hov if str(e.data).lower() == "true" else None
            safe_update(box)
        box.on_hover = hover
        return ft.PopupMenuButton(content=box, tooltip=tip, items=items)

    def tgroup(*kids, ai=False):
        return ft.Container(ft.Row(list(kids), spacing=2, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER), bgcolor=TB_AI_BG if ai else TB_GROUP, border_radius=12, padding=3)

    def tsep():
        return ft.Container(width=1, height=22, bgcolor=C["border"], margin=po(6, 0, 6, 0))

    def field_style(**kw):
        d = dict(text_size=12, height=48, border_radius=8, border_color=C["border"], focused_border_color=C["accent"], bgcolor=C["field"], color=C["text1"],
                 label_style=ft.TextStyle(color=C["text3"], size=11), content_padding=po(left=10, right=10, top=6, bottom=6))
        d.update(kw)
        return d

    def synced(handler):
        """A dropdown's select handler that always sees the chosen value: the event carries the key in e.data, while e.control.value may still be the previous one when the handler runs."""
        if handler is None:
            return None

        def h(e):
            if isinstance(getattr(e, "data", None), str) and e.data:
                e.control.value = e.data
            return handler(e)
        return h

    def dd(label, options, value, on_select, width=None, editable=False):
        d = ft.Dropdown(label=label, value=value, options=[ft.dropdown.Option(k, t) for k, t in options], on_select=synced(on_select), width=width, editable=editable,
                        **field_style(text_size=12))
        return d

    def tfield(label, value, on_change, width=None, number=False, **kw):
        return ft.TextField(label=label, value=str(value), on_change=on_change, width=width,
                            keyboard_type=ft.KeyboardType.NUMBER if number else None, **field_style(**kw))

    def confirm(title, text, ok_label, on_ok, danger=False):
        def go(e):
            page.pop_dialog()
            on_ok()
        page.show_dialog(ft.AlertDialog(title=ft.Text(title), content=ft.Text(text), bgcolor=C["panel"],
                                        actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                                                 ft.FilledButton(ok_label, on_click=go, style=ft.ButtonStyle(bgcolor=C["red"] if danger else C["accent"], color="#FFFFFF"))]))

    def cover_box(song_id: str, size: int = 48, radius: int = 8):
        """PLACEHOLDER artwork (no cover image exists for generated songs): a gradient chosen from the song id, never stored as song data."""
        pic = VX.song_cover(song_id)
        if pic:                                                   # the picture chosen in Export to Video, already cropped to a square
            return ft.Container(width=size, height=size, border_radius=radius, clip_behavior=ft.ClipBehavior.ANTI_ALIAS, content=ft.Image(src=pic, width=size, height=size, fit=ft.BoxFit.COVER))
        a, b = COVER_COLORS[zlib.crc32(song_id.encode()) % len(COVER_COLORS)]
        return ft.Container(width=size, height=size, border_radius=radius, alignment=ft.Alignment(0, 0),
                            gradient=ft.LinearGradient(begin=ft.Alignment(-1, -1), end=ft.Alignment(1, 1), colors=[a, b]),
                            content=ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, color="#FFFFFFCC", size=int(size * 0.5)))

    async def copy_text(text: str, what: str):
        try:
            await clip.set(text)
            toast(f"{what} copied")
        except Exception as ex:
            toast(f"Could not copy: {ex}", C["red"])

    # ═══ LIBRARY (data) ═════════════════════════════════════════════════════════════════════════════════════
    def refresh_lib():
        S.lib_at = time.time()
        try:
            S.lib = ST.list_songs()
        except Exception as ex:
            S.lib = []
            toast(f"Could not read the library: {ex}", C["red"])
        if S.song and not any(x["song"].id == S.song.id for x in S.lib):
            pass

    def lib_entry(song_id: str):
        return next((x for x in S.lib if x["song"].id == song_id), None)

    def best_take(entry):
        favs = [t for t in entry["takes"] if t.rating > 0]
        return (favs[-1] if favs else entry["latest"])

    def qitem(song: M.Song, t: M.Take) -> QItem:
        return QItem(song.id, t.id, song.title, f"Take {t.number:02d}", str(ST.take_path(t)))

    # ═══ EDITOR: lyrics ═════════════════════════════════════════════════════════════════════════════════════
    title_tf = ft.TextField(value="", hint_text="Song title", text_size=15, border=ft.InputBorder.NONE, color=C["text1"], dense=True, width=230,
                            content_padding=po(0, 4, 0, 4), text_style=ft.TextStyle(weight=ft.FontWeight.BOLD), hint_style=ft.TextStyle(color=C["text3"]))
    # The lyrics field is a flet-code-editor CodeEditor used as a plain-text editor: line numbers, no highlighting, no autocomplete, no folding. It scrolls by itself (there is no scroll
    # API to drive, so nothing can make it jump), keeps its caret when the program sets the text, and does not wrap: a line is as long as the user writes it.
    # A text selection lists its actions as tips above the chat box. They stay out of the editor, so the lyrics do not move while a selection is dragged. Copy, cut, paste and select-all stay on the editor's own keys and its own menu.
    _MENU_ICON = {"alternatives": ft.Icons.FIND_REPLACE_ROUNDED, "refine": ft.Icons.EDIT_NOTE_ROUNDED, "translate": ft.Icons.TRANSLATE_ROUNDED, "ask_songwriter": ft.Icons.CHAT_BUBBLE_OUTLINE_ROUNDED,
                  "rewrite": ft.Icons.AUTO_FIX_HIGH_ROUNDED, "emotion": ft.Icons.FAVORITE_OUTLINE_ROUNDED, "singability": ft.Icons.MUSIC_NOTE_ROUNDED, "phrasing": ft.Icons.FORMAT_LINE_SPACING_ROUNDED,
                  "section": ft.Icons.LABEL_OUTLINED, "cues": ft.Icons.TUNE_ROUNDED, "structure": ft.Icons.ACCOUNT_TREE_OUTLINED, "ask_producer": ft.Icons.QUEUE_MUSIC_ROUNDED}
    _selbar = {"sig": None, "sel": (0, 0)}

    def _sel_mark():
        """The user used the selection toolbar. A push that is still running must not move the caret or take the keyboard afterwards."""
        _ui["irev"] += 1
        if _ui["pushing"]:
            _release_push_waits()

    def _sel_chat(got):
        if got.switch:
            set_spec(got.specialist)                            # shows that specialist's existing conversation; the other session stays
        pick_ai_tab("chat")
        chat_tf.value = got.prefill
        chat_tf.max_lines = 8
        safe_update(chat_tf)
        try:
            page.run_task(chat_tf.focus)
        except Exception:
            pass

    def _on_sel_toolbar(key: str):
        _sel_mark()
        if not S.song:
            toast("Open or create a song first.", C["amber"]); page.run_task(_focus_editor_if_lyrics); return
        text = lyr()
        live = tuple(S.sel)
        sel = live if live[0] != live[1] else _selbar["sel"]
        got = EM.plan(key, text, sel[0], sel[1], S.spec, I18N.lang())
        log.debug("selection toolbar key=%s ok=%s route=%s action=%s switch=%s sel=%s", got.key, got.ok, got.route, got.action or got.target, got.switch, sel)
        if not got.ok:
            if got.reason:
                toast(got.reason, C["amber"])
            page.run_task(_focus_editor_if_lyrics)
            return
        if got.route == "chat":
            _sel_chat(got)
        elif got.route == "action":
            on_action_click(got.action)
        elif got.target == "alternatives":
            open_alternatives()
        elif got.target == "section":
            open_section_options()
        elif got.target == "structures":
            open_structures_dialog()

    def _sel_btn(cmd):
        """One tip above the chat box. The click is still `_on_sel_toolbar`: chat notes, actions and dialogs are unchanged."""
        return ft.Container(content=ft.Row([ft.Icon(_MENU_ICON.get(cmd.key, ft.Icons.CHEVRON_RIGHT), size=14, color=C["accent"]),
                                            ft.Text(cmd.label, size=11, color=C["text1"], no_wrap=True)], spacing=4, tight=True),
                            height=26, padding=po(left=8, right=8), border_radius=13, bgcolor=C["panel2"], border=ft.Border.all(1, C["border"]),
                            ink=True, tooltip=cmd.tip, on_click=lambda e, k=cmd.key: _on_sel_toolbar(k))

    def sync_sel_toolbar():
        """Show the actions for the current selection above the chat box. A caret, or a selection with no words, leaves them hidden."""
        text = lyr() if S.song else ""
        a, b = (S.sel if S.song else (0, 0))
        kind = EM.menu_kind(text, a, b) if S.song and b > a else "empty"
        cmds = EM.toolbar_commands(kind)
        sig = tuple(c.key for c in cmds)
        show = bool(cmds)
        if not S.song:
            _selbar["sel"] = (0, 0)
        elif show:
            _selbar["sel"] = (a, b)
        if sig == _selbar["sig"] and sel_toolbar.visible == show:
            return
        _selbar["sig"] = sig
        sel_tip_row.controls = [_sel_btn(c) for c in cmds]
        sel_toolbar.visible = show
        safe_update(sel_tip_row, sel_toolbar)

    def make_editor(value: str = ""):
        from music_studio import feature_flags as FF
        if getattr(FF, "LYRICS_TEXTFIELD", False):
            return ft.TextField(value=value, multiline=True, min_lines=None, max_lines=None, expand=True, filled=True, bgcolor=C["paper"], border=ft.InputBorder.NONE, cursor_color=C["ink"],
                                selection_color=ft.Colors.with_opacity(0.35, C["accent"]), text_style=ft.TextStyle(size=15, height=1.55, color=C["ink"]),
                                content_padding=po(left=16, right=16, top=12, bottom=12), enable_interactive_selection=True)
        ed = CodeEditor(value=value, language=CodeLanguage.PLAINTEXT, autocomplete=False, expand=True,
                        code_theme=ft.MarkdownCustomCodeTheme(root=ft.TextStyle(color=C["ink"], bgcolor=C["paper"])),
                        text_style=ft.TextStyle(size=15, height=1.55, color=C["ink"]),
                        gutter_style=GutterStyle(show_line_numbers=True, show_folding_handles=False, show_errors=False, background_color=C["paper"],
                                                 text_style=ft.TextStyle(size=12, color=C["paper_dim"])),
                        padding=po(left=4, right=16, top=12, bottom=12))
        return ed
    # One editor PER OPEN SHEET, all of them stacked in the page and all built the same way: the active one fills the Stack, the others are parked outside it (clipped, so they cannot be
    # seen or clicked). They are neither hidden (a hidden editor loses its scroll position and shares it with the next one) nor resized or made transparent (changing `expand`, `opacity`
    # or `ignore_interactions` changes the widget tree and rebuilds the control, which loses it too). So every sheet
    # keeps its own scroll position, caret and selection by itself. `lyrics_tf` always names the ACTIVE editor.
    lyrics_tf = make_editor()
    blank_ed = lyrics_tf                                                    # shown when no song is open
    editor_pool: dict = {}                                                  # (song id, sheet id) -> its CodeEditor
    PARK = 20000                                                            # how far an inactive editor is moved sideways (its width stays the same)
    editor_fresh: set = set()                                               # editors made but not given their caret yet
    editor_wrap: dict = {None: ft.Container(lyrics_tf, left=0, top=0, right=0, bottom=0)}     # the same keys -> the Container that places that sheet's editor
    editor_col = ft.Stack([editor_wrap[None]], expand=True)
    editor_holder = ft.Container(expand=True, bgcolor=C["paper"], border_radius=8, content=editor_col, clip_behavior=ft.ClipBehavior.ANTI_ALIAS)
    sheet_tabs = ft.Container(expand=True)                                  # holds a Flet Tabs: the wheel and a stray touch no longer scroll the row of sheets
    sheet_plus_holder = ft.Container()
    sheet_bar = ft.Container(height=40, padding=po(0, 0, 0, 0), content=ft.Row([sheet_tabs, sheet_plus_holder], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER))   # fixed: it never scrolls with the lyrics
    notes_txt = ft.Text("", size=11, color=C["amber"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
    sel_chip = ft.Text("", size=11, color=C["text3"])
    sel_cap = ft.Text("For this selection", size=11, weight=ft.FontWeight.W_600, color=C["text3"])
    sel_tip_row = ft.Row(spacing=4, run_spacing=4, wrap=True)
    sel_toolbar = ft.Container(visible=False, content=ft.Column([sel_cap, sel_tip_row], spacing=4, tight=True))
    # One-click access to Section Options: shown only while the caret is inside a [tag]. Selecting the tag also lists Section Options in the Lyrics Tools pane.
    tag_chip = ft.TextButton("", icon=ft.Icons.LABEL_OUTLINED, visible=False, tooltip="Edit the cues of this [tag] (Section Options)", on_click=lambda e: open_section_options(),
                             style=ft.ButtonStyle(color=C["accent"], padding=po(8, 0, 8, 0)))
    _tagchip = {"label": None}

    def refresh_tag_chip():
        ref = TP.tag_at(lyr(), S.sel[0], S.sel[1]) if S.song else None
        label = None
        if ref:
            shown = ref.original if len(ref.original) <= 34 else ref.original[:31] + "…]"
            label = f"Section Options…  {shown}"
        if label == _tagchip["label"]:
            return                                              # nothing to redraw while the caret moves inside the same tag or outside any tag
        _tagchip["label"] = label
        tag_chip.visible = label is not None
        if label:
            tag_chip.content = label
        safe_update(tag_chip)
    btn_undo = tbtn(ft.Icons.UNDO_ROUNDED, "Undo", lambda e: do_undo())
    btn_redo = tbtn(ft.Icons.REDO_ROUNDED, "Redo", lambda e: do_redo())
    dirty_txt = ft.Text("", size=11, color=C["amber"])
    _guard = {"set": False}
    # Programmatic-edit bookkeeping (everything the lyrics field and its scroll column need to tell the user's actions from ours).
    book = SelectionBook()
    _ui = {
        "epoch": 0,                          # +1 when another song is opened: work started for the previous song is void
        "rev": 0,                            # +1 for every programmatic push of the text into the field
        "busy": False, "pending": None,      # one push runs at a time; a newer request replaces one that has not started
        "irev": 0,                           # +1 when the USER types or moves the caret. An older push must not move it back. Scroll is not counted: this CodeEditor reports no scroll position.
        "arrival": None,                     # asyncio.Event a push waits on until the field reports that the pushed text arrived
        "restoring": False, "pushing": False, "sel_evt": None,
        "awaiting_echo": None, "discarded_push": None,   # text a push sent and has not seen reported; a report of it after the model moved on is not a new edit
        "focused": False, "focus_evt": None, # does the field have focus (from its own on_focus / on_blur); Event while a focus request is waiting to be confirmed
        "notes_due": 0.0, "notes_task": False, "notes_for": None,
    }
    _chat_focus = {"on": False}                  # the chat box; replaced by the real flag when the chat field is built. A push must not take the keyboard away from it.
    _chat_detach = {"key": None}                 # (song, sheet, start, end, slice) the chat bar's × removed. The editor selection is left as it is.
    _chat_snap = {"snap": None}                  # the selection captured when the user made it: song, sheet, offsets, exact slice

    def lyr() -> str:
        return S.song.lyrics if S.song else ""

    def _sync_chat_snap():
        """Remember the open sheet's selection. A caret clears it. Switching song or sheet replaces it with that sheet's own range."""
        if not S.song:
            _chat_snap["snap"] = None
            return
        _chat_snap["snap"] = CS.capture_selection(S.song.id, S.song.book.active_id, lyr(), S.sel)

    def _chat_selection_now():
        """(attach?, slice, start, end) for the song and sheet that are open.

        A snapshot from another song or sheet is not used. A snapshot whose text is no longer at
        those offsets is not used either, so a later edit cannot send the old slice.
        """
        if not S.song:
            return False, "", 0, 0
        text = lyr()
        snap = _chat_snap["snap"]
        if snap is not None:
            return CS.selection_from_snapshot(S.song.id, S.song.book.active_id, text, snap, _chat_detach["key"])
        has, selected = CS.selection_for_chat(S.song.id, S.song.book.active_id, text, S.sel, _chat_detach["key"])
        if not has:
            return False, "", 0, 0
        try:
            a, b = int(S.sel[0]), int(S.sel[1])
        except (TypeError, ValueError, IndexError):
            return False, "", 0, 0
        return True, selected, a, b

    dr_hooks: list = []                                  # the AI Music Director re-checks "is my draft still current" whenever the song changes
    ms_hooks: list = []                                  # Cover / Repaint lists refresh when the open song or its takes change. Registered after those controls exist.

    def run_ms_hooks():
        for fn in list(ms_hooks):
            try:
                fn()
            except Exception as ex:
                dbg_once("editor hook", ex)

    def mark_dirty():
        was = S.dirty
        for h in dr_hooks:
            h()
        S.dirty = True
        cs = sessions.get(S.song.id) if S.song else None
        if cs is not None and S.song.book.active_id not in cs.dirty_sheets:
            cs.dirty_sheets.add(S.song.book.active_id)
            render_sheet_bar()
        if not was:
            render_tabs()
        if dirty_txt.value != "● unsaved":                 # the indicator is only redrawn when it changes, not on every keystroke
            dirty_txt.value = "● unsaved"
            safe_update(dirty_txt)

    def sync_undo_btns():
        du, dr = not S.hist.undo, not S.hist.redo
        if btn_undo.disabled != du or btn_redo.disabled != dr:
            btn_undo.disabled, btn_redo.disabled = du, dr
            safe_update(btn_undo, btn_redo)

    def push_undo(burst: bool):
        S.hist.record(lyr(), S.sel, burst)

    def _apply_notes(text: str):
        c = K.compile_lyrics(text)
        ws = list(c.warnings)
        notes_txt.value = (f"{len(ws)} note(s): " + " | ".join(f"{('line ' + str(n) + ': ') if n else ''}{m}" for _, n, m in ws[:3])) if ws else ""
        notes_txt.tooltip = "\n".join(f"{('Line ' + str(n) + ': ') if n else ''}{m}" for _, n, m in ws) or None
        _ui["notes_for"] = text
        safe_update(notes_txt)
        if S.ws_tab == "lyrics" and S.ws_open:
            refresh_lyrics_ws()
        refresh_analysis_stale()
        if _hooks.get("tp_primary") is not None:
            _hooks["tp_primary"]()                                  # Insert Structure / Arrange with AI follows the lyrics

    async def _notes_later():
        try:
            while True:
                wait = _ui["notes_due"] - time.time()
                if wait <= 0:
                    break
                await asyncio.sleep(wait)
            if S.song:
                _apply_notes(lyr())
        finally:
            _ui["notes_task"] = False

    def refresh_notes(now: bool = False):
        """Compiler notes under the editor. While typing they wait for a short pause (one compile per pause, not per key, and one waiting task, not one per key);
        an unchanged text is never compiled again."""
        text = lyr()
        if text == _ui["notes_for"]:
            return
        if now:
            _apply_notes(text)
            return
        _ui["notes_due"] = time.time() + 0.45
        if not _ui["notes_task"]:
            _ui["notes_task"] = True
            page.run_task(_notes_later)

    def show_lyrics_to_ui(select: tuple[int, int] | None = None, focus: bool = False, keep_view: bool = False):
        """Ask for the song's lyrics to be pushed into the field after a programmatic change (a tool, undo / redo, loading a song). `select` only says that the selection S.sel is to be
        set afterwards (the value used is S.sel at that moment, so a click the user makes meanwhile is honoured). Pushes run one at a time; a request that arrives while one is
        running replaces any request still waiting. `keep_view`: the editor of a sheet that is simply shown again keeps its own caret and scroll position, so the selection is only
        sent when its text had to be replaced or the editor is new (sending it would scroll the editor to the caret)."""
        _ui["rev"] += 1
        _ui["pending"] = {"epoch": _ui["epoch"], "song": S.song.id if S.song else "", "rev": _ui["rev"], "sel": select is not None, "focus": focus, "keep": keep_view}
        if not _ui["busy"]:
            _ui["busy"] = True                                  # set here, not in the task: two runners can never start
            page.run_task(_push_runner)
        refresh_notes(now=True)

    async def _push_runner():
        try:
            while _ui["pending"] is not None:
                req = _ui["pending"]
                _ui["pending"] = None
                await _push_once(req)
        except Exception:
            log.exception("lyrics push failed")
        finally:
            _ui["busy"] = False

    def _release_push_waits():
        """Let a push that is waiting on the field continue. The caller has already recorded that the user acted, so the push stops before it moves the caret."""
        for key in ("arrival", "sel_evt"):
            ev = _ui.get(key)
            if ev is not None and not ev.is_set():
                ev.set()

    def _keep_user_caret(req, step: str, sent):
        """The selection sent with the text may still be on its way. Write the selection the user already made, once.

        The text is not sent again. The control's value can still be the pushed text while the client already shows a newer keystroke, and sending that value would wipe the keystroke.
        When the user did change the text, their caret belongs to that new text and `S.sel` may still be the previous one, so nothing is written.
        The offset is remembered as stale: the field's confirmation of this write is that echo, once, and does not count as a new click."""
        text = lyr()
        if sent is not None and text != sent:
            log.debug("push rev=%s: the user changed the text; the caret is not written back (%s)", req["rev"], step)
            return
        a = min(max(int(S.sel[0]), 0), len(text))
        b = min(max(int(S.sel[1]), 0), len(text))
        ev = (idx16(text, a), idx16(text, b))
        lyrics_tf.selection = ft.TextSelection(base_offset=ev[0], extent_offset=ev[1])
        if ev not in book.stale:
            book.stale = (list(book.stale) + [ev])[-6:]
        lyrics_tf.update()
        log.debug("push rev=%s: user selection kept at %s (%s)", req["rev"], ev, step)

    async def _push_once(req: dict):
        """Text, then selection, then focus. The CodeEditor moves its caret to the END of a text it receives, so a selection that must be placed is sent with the text and, when the
        editor will also be focused, once more after the field has reported the text (that report is the bound, not a timer). A text the field already shows is not sent again, and a
        sheet's editor that is merely shown again keeps its own caret (`keep`). Before every later selection or focus write the push checks the song, the sheet and whether the user
        has typed or selected since it began: that write is dropped. A newer request that is already waiting stops this one before the step it has not done yet."""
        def current() -> bool:
            return bool(S.song) and S.song.id == req["song"] and _ui["epoch"] == req["epoch"]

        def superseded(step: str) -> bool:
            if not current():
                log.debug("push rev=%s stop at %s: song or sheet changed", req["rev"], step)
                return True
            if _ui["pending"] is not None:
                log.debug("push rev=%s stop at %s: a newer push is waiting", req["rev"], step)
                return True
            return False

        irev0 = _ui["irev"]
        focused0 = _ui["focused"]                                # a blur after this (the right-click menu) must not be given focus back

        def user_acted() -> bool:
            return _ui["irev"] != irev0

        sent = None                                              # the text this push handed to the field, if it did
        try:
            if superseded("start"):
                return
            text = lyr()
            _ui["pushing"] = True
            changed = (lyrics_tf.value or "") != text
            log.debug("push rev=%s song=%s sheet=%s irev=%s old_len=%d new_len=%d changed=%s S.sel=%s field.sel=%s editor_focused=%s request_focus=%s keep=%s chat_focused=%s",
                      req["rev"], S.song.id, S.song.book.active_id, irev0, len(lyrics_tf.value or ""), len(text), changed, S.sel,
                      (lyrics_tf.selection.base_offset, lyrics_tf.selection.extent_offset) if lyrics_tf.selection else None, _ui["focused"], req["focus"], req["keep"], _chat_focus["on"])
            fresh_key = (S.song.id, S.song.book.active_id)
            fresh = fresh_key in editor_fresh
            editor_fresh.discard(fresh_key)
            if changed:
                if superseded("value"):
                    return
                text = lyr()                                     # the model may have moved while this push was waiting to start
                changed = (lyrics_tf.value or "") != text
            if changed:
                book.echoes.clear(); book.echo_py.clear()
                book.push_text(text)                             # caret-at-end is the field reporting this text, not a click
                sent = text
                _ui["awaiting_echo"] = text
                arrival = asyncio.Event()
                _ui["arrival"] = arrival
                lyrics_tf.value = text
                place = bool(req["sel"]) or user_acted()         # with the text, so the field does not park the caret at the end; a selection the user just made is the one that is sent
                if place:
                    a0, b0 = min(S.sel[0], len(text)), min(S.sel[1], len(text))
                    ev0 = book.push_selection(text, a0, b0) if not user_acted() else (idx16(text, a0), idx16(text, b0))
                    lyrics_tf.selection = ft.TextSelection(base_offset=ev0[0], extent_offset=ev0[1])
                lyrics_tf.update()
                log.debug("push rev=%s: value sent len=%d with_selection=%s user_acted=%s", req["rev"], len(text), place, user_acted())
                try:
                    await asyncio.wait_for(arrival.wait(), 1.5)
                    log.debug("push rev=%s: the field reported the text; field.sel=%s irev=%s", req["rev"],
                              (lyrics_tf.selection.base_offset, lyrics_tf.selection.extent_offset) if lyrics_tf.selection else None, _ui["irev"])
                except asyncio.TimeoutError:
                    log.debug("push rev=%s: the field did not report the new text in time", req["rev"])
            if superseded("after-text") or user_acted():
                if user_acted() and current():
                    _keep_user_caret(req, "after-text", sent)    # the selection sent with the text may still be in flight
                elif user_acted():
                    log.debug("push rev=%s: user acted during the push (irev %s -> %s); selection and focus stay with the user", req["rev"], irev0, _ui["irev"])
                if sent is not None and lyr() != sent:
                    _ui["discarded_push"] = sent                 # the field may still report this text; that report is not a new edit
                return
            need_sel = ES.selection_needed(bool(req["sel"]), changed, fresh, bool(req["keep"]))
            if changed and need_sel and req["focus"]:           # place the caret BEFORE focus, or a focused field reveals the caret the framework left at the end of the new text
                text = lyr()
                a1, b1 = min(S.sel[0], len(text)), min(S.sel[1], len(text))
                ev1 = book.push_selection(text, a1, b1)
                sel_evt = asyncio.Event()
                _ui["sel_evt"] = sel_evt
                lyrics_tf.selection = ft.TextSelection(base_offset=ev1[0], extent_offset=ev1[1])
                lyrics_tf.update()
                log.debug("push rev=%s: selection before focus -> %s", req["rev"], ev1)
                try:
                    await asyncio.wait_for(sel_evt.wait(), 0.8)  # the field's own report; there is no fixed sleep
                except asyncio.TimeoutError:
                    log.debug("push rev=%s: no selection report before focus", req["rev"])
            if superseded("focus") or user_acted():
                if user_acted() and current():
                    _keep_user_caret(req, "before-focus", sent)  # the selection sent before this wait may still be in flight
                elif user_acted():
                    log.debug("push rev=%s: user acted before focus (irev %s -> %s); focus stays where the user left it", req["rev"], irev0, _ui["irev"])
                return
            why = ES.focus_skip_reason(bool(req["focus"]), _chat_focus["on"], False, False, focused0 and not _ui["focused"])
            if req["focus"] and why:
                log.debug("push rev=%s: focus skipped (%s)", req["rev"], why)
            elif req["focus"]:
                await lyrics_tf.focus()
                log.debug("push rev=%s: focus requested", req["rev"])
            if superseded("selection") or user_acted():
                if user_acted():
                    log.debug("push rev=%s: user acted after focus; the caret is not moved back", req["rev"])
                return
            if need_sel:
                text = lyr()
                a, b = min(S.sel[0], len(text)), min(S.sel[1], len(text))
                ev = book.push_selection(text, a, b)
                lyrics_tf.selection = ft.TextSelection(base_offset=ev[0], extent_offset=ev[1])
                lyrics_tf.update()
                log.debug("push rev=%s: selection -> %s editor_focused=%s", req["rev"], ev, _ui["focused"])
        except Exception:
            log.exception("push rev=%s failed", req["rev"])
        finally:
            _ui["pushing"] = False; _ui["arrival"] = None; _ui["sel_evt"] = None
            refresh_tag_chip()
            sync_sel_toolbar()
            refresh_context()                                   # a programmatic edit is over: the tools may have to change

    def set_lyrics(new_text: str, sel: tuple[int, int] | None, burst: bool = False, programmatic: bool = True, refocus: bool = True):
        """refocus=False: the text and selection are pushed but the editor is not asked to take focus (a cue applied from the sidebar / a dialog: the user is not typing in the editor)."""
        if not S.song or new_text == S.song.lyrics:
            return
        push_undo(burst)
        S.song.lyrics = new_text
        if sel is not None:
            S.sel = sel
        mark_dirty()
        sync_undo_btns()
        if programmatic:
            show_lyrics_to_ui(select=S.sel, focus=refocus)
        else:
            refresh_notes()

    def on_lyrics_change(e):
        if not S.song or e.control is not lyrics_tf:                # an editor of another sheet (still in the page) is not the one being typed in
            return
        # The change event's data is the text that changed (CodeEditor sends the whole value). The control's value is a separate
        # message and can already be a later keystroke, so the event is what this call is about. The text itself is not logged.
        data = e.data if isinstance(getattr(e, "data", None), str) else None
        typed = data if data is not None else (lyrics_tf.value or "")
        action = ES.change_action(typed, S.song.lyrics, _ui.get("awaiting_echo"), _ui.get("discarded_push"))
        log.debug("change action=%s len=%d model_len=%d from_event=%s pushing=%s irev=%s song=%s sheet=%s",
                  action, len(typed), len(S.song.lyrics), data is not None, _ui["pushing"], _ui["irev"], S.song.id, S.song.book.active_id)
        if action == "late_echo":
            if _ui.get("awaiting_echo") == typed:
                _ui["awaiting_echo"] = None
            if _ui["arrival"] is not None:
                _ui["arrival"].set()
            return
        if action == "arrival":
            if _ui.get("awaiting_echo") == typed:
                _ui["awaiting_echo"] = None
            if _ui["arrival"] is not None:
                _ui["arrival"].set()                            # the field reporting a text we pushed: a push waits for this
            return
        _ui["irev"] += 1
        _ui["discarded_push"] = None
        if _ui["pushing"]:
            _release_push_waits()
        book.typed(typed)                                       # the user typed it: this is what the field holds now (proof)
        set_lyrics(typed, None, burst=True, programmatic=False)
        refresh_chat_ctx()                                      # the bar shows the slice under the current range; typing must not leave the previous slice up

    _tl_hook = {"fn": None, "tick": None}                                   # the Workspace Timeline redraws when the takes, the song or the player state change (set once the Timeline exists)
    _hooks = {"selection": None, "caret": None}                                # the Thesaurus remembers what the user selected (read-only: it never changes the field or S.sel)

    _sel_gen = {"n": 0}
    _recent_sel = {"v": None, "t": 0.0}                         # the last non-empty selection the user made; t = when it was collapsed (0 = still selected)

    def _sel_panels(kind):
        """Everything around the editor that follows the selection: the chip, the chat context, the tag chip, the Lyrics Tools sidebar, the colour column, the Thesaurus and the toolbar."""
        a, b = S.sel
        chip = f"{b - a} chars selected" if b > a else ""
        if chip != sel_chip.value:
            sel_chip.value = chip
            safe_update(sel_chip)
        refresh_chat_ctx()
        refresh_tag_chip()
        refresh_context()                                       # the Lyrics Tools sidebar (only rebuilt when what it shows changes); the Thesaurus hook below is separate and untouched
        if _hooks.get("caret") is not None:
            _hooks["caret"]()                                   # the colour column beside the editor follows the caret line
        if _hooks["selection"] is not None:
            try:
                _hooks["selection"](kind, S.sel)
            except Exception as ex:                             # a Thesaurus problem must never disturb the editor
                log.debug("thesaurus selection hook failed: %r", ex)
        sync_sel_toolbar()

    async def _sel_settle(gen: int, kind: str):
        await asyncio.sleep(0.18)
        if gen == _sel_gen["n"]:                                # a newer selection event is already waiting: it will do the work
            _sel_panels(kind)

    def on_lyrics_selection(e):
        if _guard["set"] or not S.song or e.control is not lyrics_tf:
            log.debug("selection ignored: guard=%s song=%s active_editor=%s", _guard["set"], bool(S.song), getattr(e, "control", None) is lyrics_tf)
            return
        event_sel, control_sel = getattr(e, "selection", None), lyrics_tf.selection
        ev, src = ES.pick_offsets(event_sel, control_sel)
        cause = ES.cause_name(getattr(e, "cause", None))
        log.debug("selection src=%s cause=%s event=%s control=%s pushing=%s irev=%s song=%s sheet=%s editor=%s",
                  src, cause or "-", ES.offsets_of(event_sel), ES.offsets_of(control_sel), _ui["pushing"], _ui["irev"],
                  S.song.id, S.song.book.active_id, getattr(lyrics_tf, "_i", None))
        if ev is None:
            log.debug("selection ignored: no offsets")
            return
        kind = ES.push_event_kind(ev, book.arrival_pending, book.arrival_at, book.echoes, cause, book.stale)
        sel = None
        if kind == "arrival":
            book.note_arrival()
            if _ui["arrival"] is not None:
                _ui["arrival"].set()
            if _ui["pushing"] and book.echoes and ev == book.echoes[-1] and _ui["sel_evt"] is not None:
                _ui["sel_evt"].set()
                book.take_echo(ev)
            log.debug("selection arrival during_push=%s", _ui["pushing"])
            return
        if kind == "stale_echo":
            if ev in book.stale:
                book.stale = [x for x in book.stale if x != ev]   # one report of a selection the user already replaced
            log.debug("selection stale echo ignored %s", ev)
            return
        if kind == "echo":
            if _ui["sel_evt"] is not None:
                _ui["sel_evt"].set()
            sel = book.take_echo(ev)
            log.debug("selection echo %s py=%s", ev, sel)
        else:
            _ui["irev"] += 1                                    # a click, a drag or a key moved the caret: an older push must not put it back
            if _ui["pushing"]:
                _release_push_waits()
            sel = book.adopt_user(ev, lyr())
            log.debug("selection user %s -> %s irev=%s during_push=%s", ev, sel, _ui["irev"], _ui["pushing"])
            kind = "user"
        collapsed = False
        if sel is not None and kind == "user" and _chat_focus.get("on"):
            try:
                collapsed = int(sel[0]) == int(sel[1])
            except (TypeError, ValueError, IndexError):
                collapsed = False
        if collapsed:
            log.debug("selection collapse while the chat box is focused: the lyrics selection stays")
            return
        if sel is not None:
            S.sel = sel
        if kind == "user" and sel is not None:                  # remembered for AI Actions: the click that opens a menu may collapse the selection a moment before the menu item is pressed
            try:
                if int(sel[1]) > int(sel[0]):
                    _recent_sel.update(v=(int(sel[0]), int(sel[1])), t=0.0)
                else:
                    _recent_sel["t"] = time.time()
            except (TypeError, ValueError, IndexError):
                pass
        if kind == "user":
            _chat_detach["key"] = None                          # the user selected again: that range is attached. An echo of our own write is not a new selection
            _sync_chat_snap()
        _sel_gen["n"] += 1
        gen = _sel_gen["n"]
        if kind == "user":
            page.run_task(_sel_settle, gen, kind)               # a drag fires dozens of events a second: the panels around the editor are redrawn once, when the selection has stopped moving
        else:
            _sel_panels(kind)

    def _on_ed_focus(e):
        if e.control is lyrics_tf:
            _ui["focused"] = True
            log.debug("editor focus id=%s song=%s sheet=%s pushing=%s", getattr(e.control, "_i", None), S.song.id if S.song else "", S.song.book.active_id if S.song else "", _ui["pushing"])

    def _on_ed_blur(e):
        if e.control is lyrics_tf:
            _ui["focused"] = False
            log.debug("editor blur id=%s song=%s sheet=%s pushing=%s chat_focused=%s", getattr(e.control, "_i", None), S.song.id if S.song else "", S.song.book.active_id if S.song else "", _ui["pushing"], _chat_focus["on"])

    async def _focus_editor_if_lyrics():
        """After a dialog that was about the lyrics. Not used when the chat box holds the keyboard, and not after a click in the chat or the settings."""
        if _chat_focus["on"] or not S.song or S.tab != "editor":
            log.debug("editor focus not taken after dialog: chat=%s tab=%s", _chat_focus["on"], S.tab)
            return
        try:
            await lyrics_tf.focus()
            log.debug("editor focus restored after dialog")
        except Exception as ex:
            log.debug("editor focus after dialog failed: %r", ex)

    def wire_editor(ed):
        ed.on_focus, ed.on_blur = _on_ed_focus, _on_ed_blur             # only recorded (MS_DEBUG): whether the editor holds focus while a push runs
        ed.on_change = on_lyrics_change
        ed.on_selection_change = on_lyrics_selection

    def use_editor():
        """Make the editor of the song's ACTIVE sheet the visible one (creating it the first time), and drop the editors of sheets that are no longer open or of other songs.
        The text of an editor that was kept is compared with the sheet's text by the next push, so a sheet that changed while it was in the background is brought up to date."""
        nonlocal lyrics_tf
        song = S.song
        key = (song.id, song.book.active_id) if song else None
        live = {(song.id, sid) for sid in song.book.open_ids} if song else set()
        for k in [k for k in editor_pool if k not in live]:
            editor_pool.pop(k)
            editor_col.controls.remove(editor_wrap.pop(k))
            editor_fresh.discard(k)
        if key is not None and key not in editor_pool:
            ed = make_editor(lyr())
            wire_editor(ed)
            editor_pool[key] = ed
            editor_fresh.add(key)
            editor_wrap[key] = ft.Container(ed, left=-PARK, top=0, right=PARK, bottom=0)
            editor_col.controls.append(editor_wrap[key])
        for k, w in editor_wrap.items():
            on = k == key
            w.left, w.right = (0, 0) if on else (-PARK, PARK)
        lyrics_tf = editor_pool[key] if key is not None else blank_ed
        sync_sel_toolbar()
        log.debug("use_editor key=%s pool=%s", key, list(editor_pool))
        safe_update(editor_col)

    wire_editor(lyrics_tf)

    def _changed_span(old: str, new: str):
        """Where `new` differs from `old`: the span of `new` between the common beginning and the common end. Undo / Redo put the caret there, so the field shows the lines that came back instead of jumping to the end."""
        n = min(len(old), len(new))
        p = 0
        while p < n and old[p] == new[p]:
            p += 1
        q = 0
        while q < n - p and old[len(old) - 1 - q] == new[len(new) - 1 - q]:
            q += 1
        return p, len(new) - q

    def _restore(step):
        text, sel = step
        if text != S.song.lyrics:
            sel = _changed_span(S.song.lyrics, text)
        S.song.lyrics = text
        S.sel = sel
        mark_dirty(); sync_undo_btns(); show_lyrics_to_ui(select=sel, focus=True)

    def do_undo():
        step = S.hist.step_back(lyr(), S.sel) if S.song else None
        if step:
            _restore(step)

    def do_redo():
        step = S.hist.step_forward(lyr(), S.sel) if S.song else None
        if step:
            _restore(step)

    def apply_tool(key: str):
        if not S.song:
            toast("Open or create a song first"); return
        a, b = S.sel
        log.debug("tool %s at S.sel=%s", key, S.sel)
        try:
            if key.startswith(PS.CUSTOM_PREFIX):                # a custom tool (Templates & Presets): data only, run by presets.apply_custom, applied through the same set_lyrics below
                tool = pstore.tool(key)
                if tool is None:
                    toast("This custom tool no longer exists.", C["amber"]); return
                e = PS.apply_custom(tool, lyr(), a, b)
            else:
                e = T.apply_tool(key, lyr(), a, b)
        except T.ToolError as ex:
            toast(str(ex), C["amber"]); return
        set_lyrics(e.text, (e.start, e.end))
        if e.message:
            toast(e.message)

    # toolbox ----------------------------------------------------------------------------------------------
    # Every button calls apply_tool(<key>) with the SAME key as before (T.SECTIONS names, T.VOCAL_TOOLS keys, "hold", "breakdown", "pause", "repeat", "linebreak", "build").
    # The tooltips describe what lyrics_tools.py really does; they promise nothing more.
    tool_keys: set = set()
    _TIP_SECTION = "Click to add [{n}] at the start of the line the cursor is on. No selection needed; if text is selected, it is added above the first selected line and nothing is deleted.\nExample: [{n}]"
    _TIP_CUE = ("Cursor on a section line: [Chorus] → [Chorus - {p}] (click again to remove it).\n"
                "Cursor on a lyric line, or text selected: adds a [{p}] line immediately before that lyric. The words stay.\n"
                "Note: this is a hint to the model. ACE-Step may not follow it every time.")
    _TIP_PAREN = ("Text selected: wraps it in parentheses, e.g. ooh ah → (ooh ah). Click again on wrapped text to remove the parentheses.\n"
                  "Nothing selected: inserts ( ) at the cursor with the cursor between the brackets, ready to type.\n"
                  "Note: Harmonies and Ad-lib both use ( ) in exactly the same way. The program does not tell backing harmonies from ad-libs.")
    # group -> [(sub heading or None, [(key, label, icon, tooltip)])]
    TOOL_GROUPS = [
        ("structure", "Song Structure", "Layout of the song", ft.Icons.LIBRARY_MUSIC_ROUNDED, "#6EA8FF", [
            ("Opening", [("Intro", "Intro", ft.Icons.PLAY_CIRCLE_OUTLINE, "Intro\nThe opening of the song, usually a short instrumental or melody before the singing starts.\n" + _TIP_SECTION.format(n="Intro"))]),
            ("Main Sections", [
                ("Verse", "Verse", ft.Icons.MUSIC_NOTE_ROUNDED, "Verse\nThe storytelling part with the main lyrics.\nClick to add [Verse] at the start of the line the cursor is on, numbered after the verses you already have. No selection needed.\nExample: [Verse 1], then [Verse 2]"),
                ("Pre-Chorus", "Pre-Chorus", ft.Icons.TRENDING_UP_ROUNDED, "Pre-Chorus\nA linking part that builds up the feeling before the chorus.\n" + _TIP_SECTION.format(n="Pre-Chorus")),
                ("Chorus", "Chorus", ft.Icons.STAR_ROUNDED, "Chorus\nThe main part of the song that comes back again and again: the part people sing along to.\n" + _TIP_SECTION.format(n="Chorus")),
                ("Hook", "Hook", ft.Icons.ANCHOR_ROUNDED, "Hook\nThe catchiest line or two of the song, the one people remember. It usually lives INSIDE the Chorus, so you do not need this tag for an ordinary song.\nUse it when the catchy line stands on its own: a short repeated phrase before or after the Chorus. The program treats [Hook] like a Chorus.\n" + _TIP_SECTION.format(n="Hook"))]),
            ("Transition & Ending", [
                ("Bridge", "Bridge", ft.Icons.SWAP_HORIZ_ROUNDED, "Bridge\nA part that changes the mood or melody, often placed before the last chorus.\n" + _TIP_SECTION.format(n="Bridge")),
                ("Outro", "Outro", ft.Icons.STOP_CIRCLE_OUTLINED, "Outro\nThe ending of the song.\n" + _TIP_SECTION.format(n="Outro"))])]),
        ("instrumental", "Instrumental", "Parts without singing", ft.Icons.GRAPHIC_EQ_ROUNDED, "#2DD4BF", [
            (None, [
                ("Instrumental", "Instrumental", ft.Icons.GRAPHIC_EQ_ROUNDED, "Instrumental\nAn instrumental part with no vocals, used between sung parts.\n" + _TIP_SECTION.format(n="Instrumental") + "\nDo not write lyrics under the tag."),
                ("Guitar Solo", "Guitar Solo", ft.Icons.BOLT_ROUNDED, "Guitar Solo\nA part featuring a guitar solo.\n" + _TIP_SECTION.format(n="Guitar Solo") + "\nNote: how it sounds depends on the model's interpretation."),
                ("Drum Break", "Drum Break", ft.Icons.RADIO_BUTTON_CHECKED_OUTLINED, "Drum Break\nA part where the drums stand out.\n" + _TIP_SECTION.format(n="Drum Break") + "\nNote: how it sounds depends on the model's interpretation."),
                ("breakdown", "Breakdown", ft.Icons.WAVES_ROUNDED, "Breakdown\nA part that strips back the instruments or changes the dynamics.\n" + _TIP_SECTION.format(n="Breakdown") + "\nNote: how it sounds depends on the model's interpretation.")])]),
        ("vocal", "Vocal Delivery", "How it is sung", ft.Icons.MIC_NONE_ROUNDED, "#A78BFA", [
            (None, [
                ("whisper", "Whisper", ft.Icons.VOLUME_DOWN_ROUNDED, "Whisper\nAsks for a whispered voice.\n" + _TIP_CUE.format(p="whispered")),
                ("falsetto", "Falsetto", ft.Icons.KEYBOARD_ARROW_UP_ROUNDED, "Falsetto\nAsks for a light, high head-voice.\n" + _TIP_CUE.format(p="falsetto")),
                ("belting", "Powerful Belting", ft.Icons.CAMPAIGN_ROUNDED, "Powerful Belting\nAsks for a full, powerful voice.\n" + _TIP_CUE.format(p="powerful belting")),
                ("spoken", "Spoken Word", ft.Icons.RECORD_VOICE_OVER_ROUNDED, "Spoken Word\nAsks for speaking or half-speaking instead of a sung melody.\n" + _TIP_CUE.format(p="spoken word")),
                ("soft", "Soft Vocals", ft.Icons.VOLUME_MUTE_ROUNDED, "Soft Vocals\nAsks for a soft, gentle voice.\n" + _TIP_CUE.format(p="soft vocals")),
                ("raspy", "Raspy / Gritty", ft.Icons.GRAIN_ROUNDED, "Raspy Vocal\nAsks for a rough, raspy voice. Official wording is raspy vocal. The older spelling raspy vocals is the same cue.\n" + _TIP_CUE.format(p="raspy vocal")),
                ("breathy", "Breathy Vocals", ft.Icons.AIR_ROUNDED, "Breathy Vocals\nAsks for an airy, breathy voice.\n" + _TIP_CUE.format(p="breathy vocals")),
                ("choir", "Choir / Backing", ft.Icons.GROUPS_ROUNDED, "Choir / Backing Vocals\nAsks for a choir or group of backing voices.\n" + _TIP_CUE.format(p="choir vocals")),
                ("emotional", "Emotional Vocals", ft.Icons.FAVORITE_BORDER_ROUNDED, "Emotional Vocals\nAsks for a voice full of feeling.\n" + _TIP_CUE.format(p="emotional vocals")),
                ("operatic", "Operatic Vocals", ft.Icons.THEATER_COMEDY_ROUNDED, "Operatic Vocals\nAsks for an opera-style voice.\n" + _TIP_CUE.format(p="operatic vocals")),
                ("hold", "Hold Note", ft.Icons.TIMELAPSE_ROUNDED, "Hold Note\nMarks a held note on this line. [Hold] is inserted where the cursor is. No new line is added and the words are not changed.\nThe preferred place is after the words, for example love[Hold].\nDo not leave [Hold] alone on its own line.\nNote: this is a hint to the model. ACE-Step may not follow it every time.")])]),
        ("backing", "Backing Vocals", "Extra voices", ft.Icons.GROUP_OUTLINED, "#F472B6", [
            (None, [
                ("harmonies", "Harmonies", ft.Icons.GROUP_OUTLINED, "Harmonies\nMarks words as backing vocals or harmony.\n" + _TIP_PAREN),
                ("adlib", "Ad-lib", ft.Icons.AUTO_AWESOME_ROUNDED, "Ad-lib\nMarks words as ad-libs or vocal runs.\n" + _TIP_PAREN)])]),
        ("timing", "Timing & Editing", "Pacing and quick edits", ft.Icons.TIMER_OUTLINED, "#F5B83D", [
            ("Timing", [
                ("pause", "Pause", ft.Icons.MORE_HORIZ_ROUNDED, "Pause\nAdds a pause with three dots ...\nPut the cursor where the pause should be and click (if text is selected, the dots go after the selection).\nExample: skies → skies...\nNote: a writing technique only. It does not set an exact pause length."),
                ("build", "Build Energy", ft.Icons.TRENDING_UP_ROUNDED, "Build Energy\nAsks for the part to build up in energy.\nCursor on a section line: [Chorus] → [Chorus - building energy] (replaces high / low energy if present; click again to remove it).\nCursor anywhere else: adds a [building energy] line at that spot (experimental).\nNote: a hint to the model. It may not follow it every time.")]),
            ("Editing", [
                ("repeat", "Repeat Lines", ft.Icons.REPEAT_ROUNDED, "Repeat Lines\nCopies the selected lines (or the line the cursor is on) and pastes them again right below.\nThis repeats the real text. It is not a [Repeat] tag.\nAn empty line cannot be repeated."),
                ("linebreak", "Line Break", ft.Icons.KEYBOARD_RETURN_ROUNDED, "Line Break\nStarts a new line at the cursor.\nExample: skies|never the same → two lines.\nIf text is selected, the break goes at the start of the selection and nothing is deleted.")])]),
    ]
    _open = {g[0]: g[0] == "structure" for g in TOOL_GROUPS}      # only Song Structure is open at the start

    def tool_btn(label, icon, key, color, tip):
        inner = ft.Container(content=ft.Row([ft.Icon(icon, size=15, color=color), ft.Text(label, size=12, color=C["text1"], no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, expand=True)], spacing=7),
                             height=32, padding=po(left=8, right=6), border_radius=6, bgcolor=C["panel2"], ink=True, on_click=lambda e, k=key: apply_tool(k),
                             border=ft.Border.all(1, C["border"]),
                             tooltip=ft.Tooltip(message=tip, padding=po(12, 9, 12, 9), bgcolor="#0A1426", text_style=ft.TextStyle(size=12, color=C["text1"], height=1.45), prefer_below=False,
                                                decoration=ft.BoxDecoration(bgcolor="#0A1426", border=ft.Border.all(1, C["accent"]), border_radius=8),
                                                wait_duration=ft.Duration(milliseconds=250), show_duration=ft.Duration(seconds=14), size_constraints=ft.BoxConstraints(max_width=360)))
        tool_keys.add(key)
        return ft.Draggable(group="lyrics_tool", data=key, content=inner, affinity=ft.Axis.HORIZONTAL,
                            content_feedback=ft.Container(ft.Text(label, size=12, color="#FFFFFF"), bgcolor=C["accent"], padding=po(10, 5, 10, 5), border_radius=6))

    def tool_group(gid, title, sub, icon, color, parts):
        body_ctls = []
        for part_title, tools in parts:
            if part_title:
                body_ctls.append(ft.Text(part_title, size=11, color=C["text3"], weight=ft.FontWeight.W_600))
            body_ctls.extend(tool_btn(lbl, ic, key, color, tip) for key, lbl, ic, tip in tools)
        body = ft.Column(body_ctls, spacing=4, visible=_open[gid])
        arrow = ft.Icon(ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED if _open[gid] else ft.Icons.KEYBOARD_ARROW_RIGHT_ROUNDED, size=18, color=C["text2"])

        def toggle(e):
            _open[gid] = not _open[gid]
            body.visible = _open[gid]
            arrow.icon = ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED if _open[gid] else ft.Icons.KEYBOARD_ARROW_RIGHT_ROUNDED
            safe_update(body, arrow)
        head = ft.Container(content=ft.Row([ft.Icon(icon, size=16, color=color), ft.Column([ft.Text(title, size=12, weight=ft.FontWeight.W_600, color=C["text1"], no_wrap=True),
                                                                                         ft.Text(sub, size=11, color=C["text3"], no_wrap=True)], spacing=0, expand=True), arrow], spacing=8,
                                           vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            padding=po(left=6, right=4, top=5, bottom=5), border_radius=6, ink=True, on_click=toggle, bgcolor=C["panel"], border=ft.Border.all(1, C["border"]))
        return ft.Column([head, ft.Container(body, padding=po(left=4, top=2, bottom=6))], spacing=2)

    # Custom Tools: the user's own tools (Workspace -> Templates -> Custom Tools). Global presets, rebuilt whenever they change; each button is a normal tool button with the key custom:<id>.
    pstore = PS.PresetStore.load()
    _open["custom"] = True
    custom_holder = ft.Column(spacing=6)
    ribbon_hook = {"fn": None}                                     # the Lyrics Tools ribbon is rebuilt when the custom tools change

    def rebuild_custom_tools():
        items = [(t.key, t.name, ft.Icons.EXTENSION_ROUNDED, f"{t.name}  (custom tool: {PS.TOOL_TYPES.get(t.type, t.type)})\n"
                  + (t.description + "\n" if t.description else "")
                  + (f"Inserts: {t.content}" if t.type == "snippet" else f"Wraps the selected text: {t.prefix}text{t.suffix}")) for t in pstore.tools]
        ctl = []
        if items:                                                              # tools that already exist stay usable even while their management screen is hidden
            ctl.append(tool_group("custom", "Custom Tools", "Your own tools", ft.Icons.EXTENSION_ROUNDED, "#34D399", [(None, items)]))
        elif TEMPLATE_EXTRAS_VISIBLE:
            ctl.append(ft.Text("Custom Tools: none yet", size=11, color=C["text3"]))
        if TEMPLATE_EXTRAS_VISIBLE:
            ctl.append(ft.Row([ft.TextButton("+ New Tool", on_click=lambda e: open_tool_dialog(None), style=ft.ButtonStyle(color=C["accent"], padding=po(6, 2, 6, 2))),
                           ft.TextButton("Manage Tools", on_click=lambda e: manage_presets("tools"), style=ft.ButtonStyle(color=C["text2"], padding=po(6, 2, 6, 2)))], spacing=0, wrap=True))
        custom_holder.controls = ctl
        safe_update(custom_holder)
        if ribbon_hook["fn"] is not None:
            ribbon_hook["fn"]()

    tools_col = ft.Column([ft.Row([ft.Icon(ft.Icons.TOUCH_APP_OUTLINED, size=13, color=C["text3"]), ft.Text("Hover a tool to see how to use it", size=11, color=C["text3"])], spacing=5),
                           *[tool_group(*g) for g in TOOL_GROUPS], custom_holder], spacing=8, scroll=ft.ScrollMode.AUTO)

    # drag & drop: dropping a tool on the editor applies it at the current cursor / selection (a drop point inside a TextField is not available in Flet)
    def on_tool_drop(e):
        key = getattr(getattr(e, "src", None), "data", None)
        if isinstance(key, str) and key in tool_keys:
            apply_tool(key)

    drop_target = ft.DragTarget(group="lyrics_tool", content=editor_holder, on_accept=on_tool_drop, expand=True)

    # copy / export ----------------------------------------------------------------------------------------
    async def copy_variant(kind: str):
        c = K.compile_lyrics(lyr())
        txt = {"original": c.original, "ace": c.ace, "ai": c.for_ai}[kind]
        await copy_text(txt, {"original": "Original lyrics", "ace": "ACE-Step lyrics", "ai": "Lyrics for AI"}[kind])

    def export_txt(e=None):
        if not S.song:
            return
        c = K.compile_lyrics(lyr())
        d = ST.exports_dir()
        base = ST.safe_name(S.song.title)
        p, i = d / f"{base} - lyrics.txt", 2
        while p.exists():
            p = d / f"{base} - lyrics ({i}).txt"; i += 1
        p.write_text(c.ace, encoding="utf-8")
        toast(f"Exported: {p}", C["green"])
        try: os.startfile(str(d))
        except Exception: pass

    copy_menu = tpop(ft.Icons.CONTENT_COPY_ROUNDED, "Copy / export lyrics",
                     [ft.PopupMenuItem(content=ft.Text("Copy original lyrics"), on_click=lambda e: page.run_task(copy_variant, "original")),
                      ft.PopupMenuItem(content=ft.Text("Copy ACE-Step lyrics"), on_click=lambda e: page.run_task(copy_variant, "ace")),
                      ft.PopupMenuItem(content=ft.Text("Export TXT (ACE-Step)"), on_click=export_txt)])

    # ═══ EDITOR: Section Options (cue editor for one [tag]) ═════════════════════════════════════════════════
    # The same cue editor (make_section_editor) serves the Lyrics Tools sidebar (it follows the caret) and the Section Options dialog (button, Ctrl+Shift+E, and the editor's right-click menu).
    # The tag is the one the caret reported. A click reads that caret again.
    def open_section_options(e=None):
        if not S.song:
            toast("Open a song first"); return
        text = lyr()
        a, b = S.sel
        ref = TP.tag_at(text, a, b)
        if ref is None:
            toast("Click inside a [tag] first, then open Section Options (Ctrl+Shift+E).", C["amber"]); return
        show_section_dialog(section_target(ref, TP.parse_tag(ref.inner), text.count("\n", 0, ref.start) + 1))

    def section_target(ref: TP.TagRef, parsed: TP.ParsedTag, line_no: int) -> dict:
        """Everything a Section Options draft belongs to, captured when the draft is made: the song, the SHEET, the editor epoch (it changes when another song or sheet is shown) and the
        exact tag with its place. Nothing is ever applied to a different target."""
        return {"song": S.song.id, "sheet": S.song.book.active_id, "epoch": _ui["epoch"], "ref": ref, "parsed": parsed, "line": line_no}

    def apply_section_target(tg: dict, new_tag: str | None, quiet: bool = False, sel: tuple[int, int] | None = None) -> bool:
        """Apply (new_tag) or remove (None) the tag of `tg`. The target is checked against what is shown NOW: same song, same sheet, same editor epoch, and the lyrics still hold that exact
        tag at that exact place. Anything else means nothing is applied and the user is told (the draft is never moved to the caret's new place). One undo step through set_lyrics."""
        if not SC.target_matches(tg, S.song.id if S.song else "", S.song.book.active_id if S.song else "", _ui["epoch"]):
            toast("The song or sheet changed since this tag was chosen. Nothing was changed.", C["amber"]); return False
        ref, cur = tg["ref"], lyr()
        try:
            if new_tag is None:
                new_text, caret = TP.remove_tag(cur, ref), ref.start
            else:
                new_tag = new_tag.strip()
                if new_tag == ref.original:
                    if not quiet:
                        toast("No changes")
                    return False                                     # no undo step for a change that changes nothing
                new_text, caret = TP.replace_tag(cur, ref, new_tag), ref.start + len(new_tag)
        except TP.StaleTarget:
            toast("The lyrics changed since this tag was chosen, so nothing was applied.", C["amber"]); return False
        place = (caret, caret) if sel is None else (max(0, min(int(sel[0]), len(new_text))), max(0, min(int(sel[1]), len(new_text))))
        set_lyrics(new_text, place, refocus=False)                   # one undo step; focus stays where it is (taking it scrolled the editor)
        if not quiet:
            toast("Tag removed" if new_tag is None else f"Updated {new_tag}", C["green"])
        return True

    def commit_cleared_cues(tg: dict, new_tag: str, sidebar: bool = False) -> bool:
        """Write the section tag with its cues removed. One undo step through apply_section_target. The selection is mapped onto the shorter tag."""
        cur = tg["ref"]
        new = (new_tag or "").strip()
        if new == cur.original:
            return True
        if sidebar:
            ctx_state["expect"] = {"tag": new, "start": cur.start}   # the caret stays in this tag: do not rebuild the cue list (that would jump its scroll)
        mapped = SC.map_selection(S.sel, cur.start, cur.end, cur.start + len(new))
        if apply_section_target(tg, new, sel=mapped):
            tg["ref"] = TP.TagRef(cur.start, cur.start + len(new), new)
            tg["parsed"] = TP.parse_tag(tg["ref"].inner)
            return True
        if sidebar:
            ctx_state["expect"] = None
        return False

    def make_section_editor(tg: dict, compact: bool, cue_w: int, area_h: int):
        """The cue editor for ONE tag: Recommended / All Cues / Custom, the chosen cues, the composed tag as a preview, validation. It writes nothing: the owner decides what Apply does.
        Returns a namespace of the controls and `dirty()` (the preview differs from the tag in the lyrics)."""
        ref, parsed = tg["ref"], tg["parsed"]
        standalone = parsed.kind == "standalone_cue"
        cues, original = ([], "") if standalone else TP.read_cues(parsed.cue_text)
        st = {"chosen": [c.phrase for c in cues], "custom": original, "tab": "rec", "search": "", "manual": False}
        fs = 12 if compact else 13
        preview_tf = ft.TextField(label="Tag preview" if compact else "Preview (you can edit it)", value=ref.original, **field_style(text_size=fs, height=40 if compact else 44))
        edit_tf = ft.TextField(label="Tag text", value=ref.inner, visible=standalone, **field_style(text_size=fs, height=40 if compact else 44))
        err_txt = ft.Text("", size=11, color=C["red"])
        warn_txt = ft.Text("", size=11, color=C["amber"])
        cue_area = ft.Column(spacing=2 if compact else 4, **({} if compact else {"scroll": ft.ScrollMode.AUTO, "height": area_h}), visible=not standalone)       # in the sidebar the whole panel scrolls (no scroll area inside a scroll area)
        tabs_row = ft.Row(spacing=2 if compact else 4, visible=not standalone, wrap=compact, run_spacing=2)
        apply_btn = ft.FilledButton("Apply to Section" if compact else "Apply Changes", style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"), visible=not compact)     # the sidebar applies a ticked cue at once; its button only appears after the preview text is typed by hand
        hooks = {"auto": None, "clear": lambda tag, _tg=tg: commit_cleared_cues(_tg, tag, sidebar=False)}

        def current_tag() -> str:
            if standalone:
                return f"[{(edit_tf.value or '').strip()}]"
            cue_text = TP.compose_cue_text([CL.BY_PHRASE[p] for p in st["chosen"] if p in CL.BY_PHRASE], st["custom"])
            return TP.build_tag(parsed.name, cue_text)

        def validate(tag: str):
            ok_ = TP.valid_tag_text(tag)
            err_txt.value = "" if ok_ else "The tag must be one [ ... ] with no brackets or line breaks inside."
            n = len(tag.strip("[] ").partition(" - ")[2].replace(" and ", ",").replace(" with ", ",").split(",")) if " - " in tag else 0
            warn_txt.value = f"{n} cues in one tag is a lot: the model may follow only some of them." if n > TP.MANY_CUES else ""
            apply_btn.disabled = not ok_ or (compact and tag.strip() == tg["ref"].original)        # the sidebar's Apply wakes up only when there is something to apply
            if parsed.kind == "section":
                clear_btn.disabled = not SC.clear_enabled(tag, tg["ref"].original)
            safe_update(err_txt, warn_txt, apply_btn, clear_btn)

        def refresh_preview():
            if not standalone:
                preview_tf.value = current_tag()
                st["manual"] = False
                safe_update(preview_tf)
                validate(preview_tf.value)

        def on_preview(ev):
            st["manual"] = True
            if compact and not apply_btn.visible:
                apply_btn.visible = True
                safe_update(apply_btn)
            validate(preview_tf.value or "")

        def on_edit(ev):
            preview_tf.value = current_tag()
            safe_update(preview_tf)
            validate(preview_tf.value)
        preview_tf.on_change = on_preview
        edit_tf.on_change = on_edit

        def toggle(phrase: str, on: bool):
            if on and phrase not in st["chosen"]:
                st["chosen"].append(phrase)
            elif not on and phrase in st["chosen"]:
                st["chosen"].remove(phrase)
            refresh_preview()
            if hooks["auto"] is not None and not standalone:
                hooks["auto"]()

        def cue_chip(c: CL.Cue):
            on0 = c.phrase in st["chosen"]
            check = ft.Icon(ft.Icons.CHECK_ROUNDED, size=13, color="#FFFFFF", visible=on0)
            label = ft.Text(c.label, size=11, color="#FFFFFF" if on0 else C["text1"], no_wrap=True)
            chip = ft.Container(ft.Row([check, label], spacing=3, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER), height=26, padding=po(8, 5, 8, 0), border_radius=13, ink=True,       # no `alignment`: it would stretch the chip over the whole row
                                bgcolor=C["accent"] if on0 else C["panel2"], border=ft.Border.all(1, C["accent"] if on0 else C["border"]), tooltip=f"adds “{c.phrase}” to the tag. {c.guidance}")

            def flip(ev):
                now = c.phrase not in st["chosen"]
                check.visible = now
                label.color = "#FFFFFF" if now else C["text1"]
                chip.bgcolor = C["accent"] if now else C["panel2"]
                chip.border = ft.Border.all(1, C["accent"] if now else C["border"])
                safe_update(chip)
                toggle(c.phrase, now)
            chip.on_click = flip
            return chip

        def cue_box(c: CL.Cue):
            if compact:
                return cue_chip(c)
            cb = ft.Checkbox(label=c.label, value=c.phrase in st["chosen"], on_change=lambda ev, p=c.phrase: toggle(p, bool(ev.control.value)),
                             **({"label_style": ft.TextStyle(size=12, color=C["text1"])} if compact else {}))
            return ft.Container(cb, width=cue_w, height=30 if compact else None)

        def grouped(cue_list: list):
            if compact:
                return [ft.Row([cue_box(c) for c in cue_list], wrap=True, spacing=4, run_spacing=4)]
            out = []
            for cid, cname in CL.CATEGORIES:
                items = [c for c in cue_list if c.category == cid]
                if items:
                    out += [ft.Text(cname.upper() if compact else cname, size=10 if compact else 12, weight=ft.FontWeight.BOLD, color=C["text3"] if compact else C["text2"]),
                            ft.Row([cue_box(c) for c in items], wrap=True, spacing=4, run_spacing=0)]
            return out

        def build_area():
            tabs_row.controls = seg_buttons([("rec", "Recommended" if not compact else "Rec."), ("all", "All Cues" if not compact else "All"), ("custom", "Custom")], st["tab"], pick_tab)
            if st["tab"] == "rec":
                rec = CL.recommended(parsed.name)
                shown = rec + [CL.BY_PHRASE[p] for p in st["chosen"] if p in CL.BY_PHRASE and CL.BY_PHRASE[p] not in rec]      # a chosen cue never disappears from view
                cue_area.controls = grouped(shown) or [ft.Text("No recommendations for this tag. Use All Cues or Custom.", size=12, color=C["text3"])]
            elif st["tab"] == "all":
                q = st["search"].strip().lower()
                s_tf = ft.TextField(hint_text="Search cues...", value=st["search"], prefix_icon=ft.Icons.SEARCH_ROUNDED, **field_style(height=38), on_change=on_search)
                cue_area.controls = [s_tf] + (grouped([c for c in CL.CUES if not q or q in c.label.lower() or q in I18N.tr(c.label).lower()]) or [ft.Text("No cue matches.", size=12, color=C["text3"])])
            else:
                note = []
                if original:
                    note = [ft.Text(f"The existing cue text could not be split into known cues, so it is kept exactly as written: “{original}”. Edit it here if you want to change it.", size=11, color=C["text3"])]
                cue_area.controls = note + [ft.TextField(label="Custom Cue", value=st["custom"], hint_text="for example: soft bells", **field_style(height=48), on_change=on_custom)]
            safe_update(tabs_row, cue_area)

        def on_search(ev):
            st["search"] = ev.control.value or ""
            q = st["search"].strip().lower()
            cue_area.controls = [cue_area.controls[0]] + (grouped([c for c in CL.CUES if not q or q in c.label.lower() or q in I18N.tr(c.label).lower()]) or [ft.Text("No cue matches.", size=12, color=C["text3"])])
            safe_update(cue_area)

        def on_custom(ev):
            st["custom"] = ev.control.value or ""
            refresh_preview()

        def pick_tab(k: str):
            st["tab"] = k
            build_area()

        def show_cleared(bare: str):
            nonlocal original
            original = ""                                        # the custom tab must not keep describing cues that were just removed
            st["chosen"] = []
            st["custom"] = ""
            st["manual"] = False
            preview_tf.value = bare
            safe_update(preview_tf)
            if not standalone:
                build_area()
            validate(bare)

        def on_clear(ev=None):
            """One click: drop every cue on this section. The name and the sung lines stay. No confirm, no model."""
            if clear_btn.disabled or parsed.kind != "section":
                return
            bare = SC.bare_section_tag(tg["ref"].original)
            if bare is None:
                return
            if bare != tg["ref"].original:
                fn = hooks.get("clear")
                if fn is None or not fn(bare):
                    return                                         # another song, a stale tag: the draft stays so the preview does not lie
            show_cleared(bare)

        clear_btn = ft.TextButton("Clear Cues", icon=ft.Icons.CLEAR_ALL_ROUNDED, icon_color=C["text2"], height=28, key="clear_cues",
                                   style=ft.ButtonStyle(padding=po(6, 0, 8, 0), color=C["text2"], text_style=ft.TextStyle(size=12)),
                                   tooltip="Remove every cue from this section. The section name and the lyrics stay.",
                                   disabled=not SC.clear_enabled(ref.original, ref.original), visible=parsed.kind == "section", on_click=on_clear)
        clear_row = ft.Row([ft.Container(expand=True), clear_btn], spacing=0, visible=parsed.kind == "section")

        def start():
            if not standalone:
                build_area()
            validate(preview_tf.value or "")

        def dirty() -> bool:
            return (preview_tf.value or "").strip() != tg["ref"].original

        return types.SimpleNamespace(tg=tg, hooks=hooks, standalone=standalone, preview_tf=preview_tf, edit_tf=edit_tf, err_txt=err_txt, warn_txt=warn_txt, cue_area=cue_area, tabs_row=tabs_row,
                                     clear_row=clear_row, apply_btn=apply_btn, start=start, dirty=dirty)

    def show_section_dialog(tg: dict):
        ref, parsed = tg["ref"], tg["parsed"]
        ed = make_section_editor(tg, compact=False, cue_w=165, area_h=250)

        def close(ev=None):
            page.pop_dialog()                                    # Cancel / close: nothing was changed
            page.run_task(_focus_editor_if_lyrics)               # the dialog had the keyboard; the lyrics are what the user was editing
        _finishing = {"on": False}

        async def finish_async(new_tag: str | None):
            """Apply first, close the dialog when the editor has received the new text AND its selection. When the dialog closes, Flutter hands the focus back to the editor; if that happened in the
            middle of the push the editor would be focused while it holds a half-applied state and could scroll to it. (Waits for the push to finish: no timer is used to force a scroll.)"""
            if _finishing["on"]:
                return                                            # a second click on Apply while the first is still working: nothing is applied twice
            _finishing["on"] = True
            try:
                applied = apply_section_target(tg, new_tag)          # checks song, sheet, epoch and the exact tag again
                if not applied:
                    return                                        # refused (or "No changes"): the dialog stays open and the message is on screen
                for _ in range(24):                               # at most ~1.2 s: the dialog closes as soon as the editor has the text (it normally takes well under a second)
                    if not _ui["busy"] and _ui["pending"] is None:
                        break
                    await asyncio.sleep(0.05)
                page.pop_dialog()
                await _focus_editor_if_lyrics()
            finally:
                _finishing["on"] = False

        def finish(new_tag: str | None):
            page.run_task(finish_async, new_tag)

        ed.apply_btn.on_click = lambda ev: finish(ed.preview_tf.value or "")
        actions = [ft.TextButton("Cancel", on_click=close)]
        if ed.standalone or parsed.kind == "custom":
            actions.append(ft.TextButton("Remove Tag", on_click=lambda ev: finish(None), style=ft.ButtonStyle(color=C["red"])))
        actions.append(ed.apply_btn)
        kind_txt = {"section": "section", "standalone_cue": "cue tag (not a section)", "custom": "custom tag"}[parsed.kind]
        body = ft.Column([ft.Text(f"Tag: {parsed.name}", size=15, weight=ft.FontWeight.BOLD, color=C["text1"]),
                          ft.Text(f"Line {tg['line']} · {kind_txt}", size=11, color=C["text3"]),
                          ed.tabs_row, ed.clear_row, ed.cue_area, ed.edit_tf,
                          ft.Divider(height=8, color=C["border"]),
                          ft.Text("CURRENT", size=10, weight=ft.FontWeight.BOLD, color=C["text3"]), ft.Text(ref.original, size=13, color=C["text1"], selectable=True),
                          ed.preview_tf, ed.warn_txt, ed.err_txt], spacing=6, tight=True, scroll=ft.ScrollMode.AUTO)
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Row([ft.Text("Section Options"), ft.Container(expand=True), ibtn(ft.Icons.CLOSE_ROUNDED, "Close", close)]),
                                        content=ft.Container(body, width=560, height=dlg_h(430)), actions=actions))
        ed.start()

    # ═══ EDITOR: the Lyrics Tools sidebar follows the caret (cursor_context.py): a section tag -> its cue editor, sung lyrics / a selection / an empty line -> the tools that fit, any other tag
    # -> a small tag editor. The sidebar is only rebuilt when what it shows changes (the context signature), never on every key; the old toolbox stays under "All Tools". ═════════════
    TOOL_INFO = {key: (label, icon, tip, g[4]) for g in TOOL_GROUPS for _t, tools in g[5] for key, label, icon, tip in tools}
    CTX_VOCAL = ["whisper", "soft", "breathy", "belting", "falsetto", "spoken", "raspy", "emotional"]
    CTX_BACKING = ["harmonies", "adlib", "hold", "build"]
    CTX_SECTIONS = ["Verse", "Pre-Chorus", "Chorus", "Hook", "Bridge", "Intro", "Outro"]

    def ctx_tools(title: str, keys: list):
        return ft.Column([ft.Text(title, size=10, weight=ft.FontWeight.BOLD, color=C["text3"]), *[tool_btn(TOOL_INFO[k][0], TOOL_INFO[k][1], k, TOOL_INFO[k][3], TOOL_INFO[k][2]) for k in keys]], spacing=4)

    ctx_hold_hint = ft.Text("Hold Note: put the cursor where [Hold] should sit on this line, preferably after the words.", size=10, color=C["text3"])
    ctx_view_lyrics = ft.Column([ctx_tools("VOCAL DELIVERY", CTX_VOCAL), ctx_tools("BACKING & EXPRESSION", CTX_BACKING), ctx_hold_hint, ctx_tools("QUICK INSERT SECTION", CTX_SECTIONS)], spacing=8, visible=False)
    ctx_view_empty = ft.Column([ctx_tools("QUICK INSERT SECTION", CTX_SECTIONS), ctx_tools("VOCAL DELIVERY", CTX_VOCAL)], spacing=8, visible=False)
    ctx_title = ft.Text("", size=10, weight=ft.FontWeight.BOLD, color=C["accent"])
    ctx_sub = ft.Text("", size=13, weight=ft.FontWeight.W_600, color=C["text1"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
    ctx_dynamic = ft.Column(spacing=6, visible=False)                        # the cue editor / tag editor of the tag at the caret
    ctx_state = {"sig": None, "editor": None, "tag": ""}

    def _cue_cols() -> tuple:
        inner = TOOLS_W - 18                                                 # the panel's padding and the scroll bar
        cols = 2 if inner >= 330 else 1                                      # one column in a narrow sidebar, two in a wide one
        return cols, int(inner / cols) - 4

    def ctx_section_view(c: "CC.CursorContext", text: str):
        ref = TP.TagRef(c.tag_range[0], c.tag_range[1], c.tag_text)
        parsed = TP.parse_tag(ref.inner)
        tg = section_target(ref, parsed, c.line_number)
        cols, w = _cue_cols()
        ed = make_section_editor(tg, compact=True, cue_w=w, area_h=210)
        def do_apply(new_tag, auto: bool = False):
            cur = tg["ref"]
            if new_tag is None:
                if apply_section_target(tg, None):
                    ctx_state["editor"] = None                               # removed: nothing is left to discard
                return
            new = new_tag.strip()
            if auto and (not TP.valid_tag_text(new) or new == cur.original):
                return
            ctx_state["expect"] = {"tag": new, "start": cur.start}           # set BEFORE the lyrics change: the caret move that follows must not rebuild this panel (it would reset its scroll)
            if apply_section_target(tg, new, quiet=auto):
                tg["ref"] = TP.TagRef(cur.start, cur.start + len(new), new)  # the next tick works on the tag as it is now
                ed.apply_btn.visible = False
                safe_update(ed.apply_btn)
            else:
                ctx_state["expect"] = None
        ed.hooks["auto"] = lambda: do_apply(ed.preview_tf.value or "", auto=True)
        ed.hooks["clear"] = lambda tag, _tg=tg: commit_cleared_cues(_tg, tag, sidebar=True)
        ed.apply_btn.on_click = lambda ev: do_apply(ed.preview_tf.value or "")
        rm = ft.TextButton("Remove tag", on_click=lambda ev: do_apply(None), style=ft.ButtonStyle(color=C["red"], padding=po(4, 0, 4, 0)))
        if ed.standalone:
            body = [ed.edit_tf, ed.preview_tf, ed.warn_txt, ed.err_txt, ft.Row([ed.apply_btn, rm], spacing=4, wrap=True)]
        else:
            body = [ed.tabs_row, ed.preview_tf, ed.warn_txt, ed.err_txt, ft.Row([ed.apply_btn, rm], spacing=4, wrap=True), ed.clear_row, ed.cue_area]      # Clear Cues sits on the cue list; the preview stays in view
        ctx_state["editor"] = ed
        ctx_dynamic.controls = body
        ctx_dynamic.visible = True
        ed.start()

    def ctx_custom_view(c: "CC.CursorContext"):
        ctx_state["editor"] = None
        ctx_dynamic.controls = [ft.Text("This is your own tag, not a section, so no cues are suggested for it.", size=11, color=C["text3"]),
                                ft.OutlinedButton("Edit this tag…", icon=ft.Icons.LABEL_OUTLINED, height=32, on_click=lambda e: open_section_options())]
        ctx_dynamic.visible = True

    def refresh_context(force: bool = False):
        """Show the tools that fit the caret. Cheap when nothing changed: the context is worked out (one tag scan, like the tag chip) and compared with the one on show."""
        pv_follow()                                                          # the Preview tab (when it is open) follows the caret too; it rebuilds only when the section changes
        song = S.song
        text = lyr() if song else ""
        a, b = S.sel if song else (0, 0)
        c = CC.resolve(text, a, b, has_song=bool(song))
        sheet = song.book.active_id if song else ""
        sig = c.signature(song.id if song else "", sheet)
        if c.context == CC.TEXT_SELECTION:                                   # the length is the only thing that changes while the selection grows: a text, not a rebuild
            n = c.selection[1] - c.selection[0]
            sub = f"{n} chars selected" + (" · includes tags" if c.crosses_tags else "")
            if ctx_sub.value != sub:
                ctx_sub.value = sub
                safe_update(ctx_sub)
        exp = ctx_state.get("expect")
        if exp and not force and c.context == CC.SECTION_TAG and c.tag_text == exp["tag"] and c.tag_range[0] == exp["start"] and ctx_state["editor"] is not None:
            ctx_state["sig"], ctx_state["tag"], ctx_state["expect"] = sig, c.tag_text, None            # our own apply: the panel already shows this tag, keep it (and its scroll position)
            return
        if sig == ctx_state["sig"] and not force:
            return
        ctx_state["expect"] = None
        old = ctx_state["editor"]
        if old is not None and old.dirty() and ctx_state["tag"]:                  # a draft that was not applied is not carried to another place: the user is told it is gone
            toast(f"The unapplied change to {ctx_state['tag']} was discarded.", C["amber"])
        ctx_state["sig"], ctx_state["editor"], ctx_state["tag"] = sig, None, ""
        ctx_view_lyrics.visible = ctx_view_empty.visible = ctx_dynamic.visible = False
        if c.context == CC.NO_SONG:
            ctx_title.value, ctx_sub.value = "NO SONG", "Open or create a song to use the tools."
        elif c.context == CC.SECTION_TAG or c.context == CC.STANDALONE_TAG:
            ctx_title.value = "EDITING SECTION" if c.context == CC.SECTION_TAG else "EDITING CUE TAG"
            ctx_sub.value = f"{c.tag_name} · Line {c.line_number}"
            ctx_state["tag"] = c.tag_text
            ctx_section_view(c, text)
        elif c.context == CC.CUSTOM_TAG:
            ctx_title.value, ctx_sub.value = "CUSTOM TAG", f"{c.tag_name} · Line {c.line_number}"
            ctx_custom_view(c)
        elif c.context == CC.TEXT_SELECTION:
            ctx_title.value = "SELECTION"
            n = c.selection[1] - c.selection[0]
            ctx_sub.value = f"{n} chars selected" + (" · includes tags" if c.crosses_tags else "")
            ctx_view_lyrics.visible = True
        elif c.context == CC.EMPTY_LINE:
            ctx_title.value, ctx_sub.value = "EMPTY LINE", "Add a section or a vocal cue here."
            ctx_view_empty.visible = True
        else:
            ctx_title.value, ctx_sub.value = "SUNG LYRICS", "Vocal delivery for this line."
            ctx_view_lyrics.visible = True
        safe_update(ctx_title, ctx_sub, ctx_view_lyrics, ctx_view_empty, ctx_dynamic)

    # ═══ EDITOR: songs list + save ══════════════════════════════════════════════════════════════════════════
    song_list = ft.ListView(expand=True, spacing=6, padding=po(0, 0, 4, 0))
    search_tf = ft.TextField(hint_text="Search songs...", prefix_icon=ft.Icons.SEARCH_ROUNDED, on_change=lambda e: on_search_change(e),
                             **field_style(expand=True, hint_style=ft.TextStyle(color=C["text3"]), height=38))

    def collection_songs(name: str = "all"):
        """The songs of a collection in the current sort order. Search is a screen filter and is not applied here."""
        out = [x for x in S.lib if name == "all" or x["favorite"]]
        if S.sort == "title":
            out.sort(key=lambda x: x["song"].title.lower())
        elif S.sort == "takes":
            out.sort(key=lambda x: -len(x["takes"]))
        elif S.sort == "created":
            out.sort(key=lambda x: x["song"].created, reverse=True)
        else:
            out.sort(key=lambda x: x["song"].updated, reverse=True)
        return out

    def filtered_lib(name: str = "all"):
        q = S.search.strip().lower()
        return [x for x in collection_songs(name) if not q or q in x["song"].title.lower() or q in x["song"].settings.genre.lower()]

    def song_row(x):
        s: M.Song = x["song"]
        active = S.song is not None and S.song.id == s.id
        n = len(x["takes"])
        dur = x["latest"].duration if x["latest"] else 0
        menu = ft.PopupMenuButton(icon=ft.Icons.MORE_HORIZ_ROUNDED, icon_color=C["text2"], icon_size=18, items=[
            ft.PopupMenuItem(content=ft.Text("Rename"), on_click=lambda e, i=s.id: rename_dialog(i)),
            ft.PopupMenuItem(content=ft.Text("Save a copy (Save As)"), on_click=lambda e, i=s.id: save_as_dialog(i)),
            ft.PopupMenuItem(content=ft.Text("Delete"), on_click=lambda e, i=s.id: delete_song_confirm(i))])
        return ft.Container(padding=8, border_radius=10, ink=True, on_click=lambda e, i=s.id: open_song(i),
                            bgcolor=C["accent_soft"] if active else "#00000000", border=ft.Border.all(1, C["accent"] if active else "#00000000"),
                            content=ft.Row([cover_box(s.id, 50), ft.Column([ft.Text(s.title, size=14, weight=ft.FontWeight.W_600, color=C["text1"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                                                             ft.Text(s.settings.genre or "-", size=12, color=C["text2"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                                                             ft.Text(f"{n} take{'s' if n != 1 else ''}" + (f" • {fmt_dur(dur)}" if dur else ""), size=11, color=C["text3"])]
                                                                            + ([ft.Text("⚠ Data file damaged (backup saved)", size=11, color=C["red"], tooltip="\n".join(x["warnings"]))] if x.get("warnings") else []),
                                                                            spacing=1, expand=True), menu], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER))

    def rebuild_song_list():
        rows = [song_row(x) for x in filtered_lib()]
        song_list.controls = rows or [ft.Text("No songs yet. Press New Song.", size=12, color=C["text3"])]
        safe_update(song_list)

    # song lifecycle ---------------------------------------------------------------------------------------
    def is_dirty(song_id: str) -> bool:
        if S.song and S.song.id == song_id:
            return S.dirty
        sess = sessions.get(song_id)
        return bool(sess and sess.dirty)

    def park():
        """Store the state of the song shown in the editor in its session, before another song (or My Music) takes the editor's place."""
        sess = sessions.get(S.song.id) if S.song else None
        if sess:
            sess.song, sess.dirty, sess.hist, sess.sel, sess.chat = S.song, S.dirty, S.hist, S.sel, S.chat

    def attach(sess: Sess, park_first: bool = True):
        """Show a session in the editor. Nothing is read from disk except the takes: the draft, history, caret and chat come from the session."""
        if park_first:
            park()
        song = sess.song
        S.song, S.dirty, S.hist, S.sel, S.chat = song, sess.dirty, sess.hist, sess.sel, sess.chat
        S.takes, S.runs = ST.load_takes(song.id)
        _ui.update(epoch=_ui["epoch"] + 1, arrival=None, pending=None, irev=_ui["irev"] + 1, focused=False, notes_for=None, awaiting_echo=None, discarded_push=None)
        book.reset()
        use_editor()
        _guard["set"] = True
        title_tf.value = song.title
        _guard["set"] = False
        dirty_txt.value = "● unsaved" if sess.dirty else ""
        a, b = sess.sel
        sel_chip.value = f"{b - a} chars selected" if b > a else ""
        _sync_chat_snap()
        chat_busy.visible = S.chat_busy_song == song.id
        show_lyrics_to_ui(select=sess.sel, keep_view=True)
        build_settings(); rebuild_output(); rebuild_chat(); rebuild_song_list(); sync_undo_btns(); update_generate_button()
        safe_update(title_tf, dirty_txt, sel_chip, chat_busy)
        refresh_tag_chip(); refresh_context(force=True)
        render_analysis()
        render_tabs()
        render_sheet_bar()
        run_ms_hooks()

    def load_song_into_editor(song: M.Song, dirty: bool = False):
        """A song that is not open yet (or whose recovered autosave replaces its session): new session, new tab if needed."""
        park()
        sess = Sess(song, dirty)
        sessions[song.id] = sess
        load_sess_analysis(sess)
        if song.id not in open_tabs:
            open_tabs.append(song.id)
        attach(sess, park_first=False)

    busy_state = {"on": False}
    busy_text = ft.Text("", size=14, weight=ft.FontWeight.W_600, color="#FFFFFF")
    busy_layer = ft.Container(visible=False, expand=True, bgcolor="#99000000", alignment=ft.Alignment.CENTER, on_click=lambda e: None,        # covers the whole window and takes every click: nothing else can be pressed while a tab or a song is opening
                              content=ft.Container(padding=po(28, 22, 28, 22), border_radius=14, bgcolor=C["panel"], border=ft.Border.all(1, C["border"]),
                                                   content=ft.Column([ft.ProgressRing(width=40, height=40, stroke_width=4, color=C["accent"]), busy_text], spacing=14, tight=True,
                                                                     horizontal_alignment=ft.CrossAxisAlignment.CENTER)))
    page.overlay.append(busy_layer)

    def busy_run(label: str, fn, *args):
        """Run a slow step (opening a song, switching a tab) behind a centred spinner that blocks the window. The click handler only marks the window busy and returns, so the window is free to draw the spinner;
        the work then starts a moment later (after the spinner has been sent), and the spinner is removed when it ends, even when it fails. A step started while another one is running is ignored."""
        if busy_state["on"]:
            return
        busy_state["on"] = True
        page.run_task(_busy_task, label, fn, args)

    async def _busy_task(label: str, fn, args):
        try:
            busy_text.value = label
            busy_layer.visible = True
            try:
                page.update()                                    # the whole page: the spinner is on screen before the slow part begins
            except Exception:
                safe_update(busy_layer)
            await asyncio.sleep(0.12)                            # lets the window draw the spinner first
            fn(*args)
        except Exception as ex:
            dbg_once("busy step", ex, warn=True)
            toast(f"Could not finish: {ex}", C["red"])
        finally:
            busy_layer.visible = False
            busy_state["on"] = False
            try:
                page.update()
            except Exception:
                safe_update(busy_layer)

    def activate(song_id: str):
        sess = sessions.get(song_id)
        if not sess:
            return
        if S.song and S.song.id == song_id and S.tab == "editor":
            return
        busy_run("Opening…", _activate_now, sess)

    def _activate_now(sess):
        attach(sess)
        set_tab("editor")

    def go_library(e=None):
        if S.tab == "library":
            return
        busy_run("Opening My Music…", set_tab, "library")

    def open_song(song_id: str, then_tab: str | None = None):
        """Edit Song: one tab per song id; an open song is only activated, so nothing unsaved is touched."""
        if song_id in sessions:
            activate(song_id)
            return
        busy_run("Opening song…", _open_song_now, song_id)

    def _open_song_now(song_id: str):
        try:
            song = ST.load_song(song_id)
        except Exception as ex:
            toast(f"Cannot open this song: {ex}", C["red"]); return
        auto = ST.newer_autosave(song_id)
        load_song_into_editor(song)
        set_tab("editor")
        if auto and (auto.lyrics != song.lyrics or auto.title != song.title or auto.settings != song.settings or (auto.book is not None and SH.signature(auto) != SH.signature(song))):
            def recover(e):
                page.pop_dialog(); load_song_into_editor(auto, dirty=True)

            def drop(e):
                page.pop_dialog(); ST.discard_autosave(song_id)
            page.show_dialog(ft.AlertDialog(title=ft.Text("Recover unsaved changes?"), bgcolor=C["panel"], modal=True,
                                            content=ft.Text("This song has an autosaved version newer than the saved one (the program closed before saving)."),
                                            actions=[ft.TextButton("Discard it", on_click=drop), ft.FilledButton("Recover", on_click=recover)]))

    # ═══ LYRICS SHEETS (tabs under the editor): several drafts inside ONE song. A Song Tab (top bar) = another song project; a Sheet Tab = another draft of THIS song. ══════════════════
    def sheet_key(sess) -> str:
        """Which analysis file belongs to the active sheet: "" = analysis.json (the song as it was before sheets), else the sheet id."""
        b = sess.song.book
        return "" if b.active_id == b.primary_id else b.active_id

    def sheet_rt_save(sess):
        """Keep what the editor holds for the sheet that is being left (history, caret, scroll, analysis); the text and options themselves are flushed by sheets.py."""
        sess.rt[sess.song.book.active_id] = {"hist": S.hist, "sel": S.sel, "scroll": 0.0, "analysis": sess.analysis, "rhyme": sess.rhyme, "rhythm": sess.rhythm,
                                             "thes": sess.thes, "thes_sel": sess.thes_sel, "an_corr": sess.an_corr,
                                             "review": sess.review, "review_dec": sess.review_dec, "report_on": sess.report_on, "report_hash": sess.report_hash, "specreports": sess.specreports, "fd_status": sess.fd_status, "fd_undone": sess.fd_undone}

    def sheet_rt_load(sess):
        r = sess.rt.pop(sess.song.book.active_id, None)
        if r is None:                                           # a sheet seen for the first time (or after a restart): empty history, caret at the start, its own analysis from disk
            sess.hist, sess.sel, sess.scroll = EditHistory(), (0, 0), 0.0
            sess.analysis = sess.rhyme = sess.rhythm = sess.thes = sess.thes_sel = sess.review = None
            sess.review_dec = []
            sess.report_on, sess.report_hash = False, ""
            sess.specreports, sess.fd_status, sess.fd_undone = {}, {}, {}
            sess.an_corr = {}
            load_sess_analysis(sess)
        else:
            sess.hist, sess.sel, sess.scroll = r["hist"], r["sel"], r["scroll"]
            sess.analysis, sess.rhyme, sess.rhythm, sess.thes, sess.thes_sel, sess.an_corr = r["analysis"], r["rhyme"], r["rhythm"], r["thes"], r["thes_sel"], r["an_corr"]
            sess.review, sess.review_dec = r.get("review"), r.get("review_dec", [])
            sess.report_on, sess.report_hash = r.get("report_on", False), r.get("report_hash", "")
            sess.specreports, sess.fd_status, sess.fd_undone = r.get("specreports", {}), r.get("fd_status", {}), r.get("fd_undone", {})
        S.hist, S.sel = sess.hist, sess.sel

    def refresh_editor_for_sheet(sess):
        _ui.update(epoch=_ui["epoch"] + 1, arrival=None, pending=None, irev=_ui["irev"] + 1, focused=False, notes_for=None, awaiting_echo=None, discarded_push=None)     # work started for the sheet that was left is void
        book.reset()
        use_editor()
        a, b = S.sel
        sel_chip.value = f"{b - a} chars selected" if b > a else ""
        _sync_chat_snap()
        show_lyrics_to_ui(select=S.sel, keep_view=True)
        build_settings(); sync_undo_btns(); update_generate_button()
        safe_update(sel_chip)
        refresh_tag_chip(); refresh_context(force=True); refresh_chat_ctx(); render_analysis()
        run_ms_hooks()

    def sheet_change(mutator, structural: bool = True):
        """Run an operation of sheets.py that may change the active sheet. The editor state of the sheet that is left is kept, the new one is loaded, and switching itself creates
        no undo step. Returns the operation's result, or None when it was refused (a sentence is shown and nothing changed)."""
        if not S.song:
            return None
        sess = sessions.get(S.song.id)
        if sess is None:
            return None
        if S.an_busy == S.song.id:
            toast("An analysis of this song is still running. Switch sheets when it has finished.", C["amber"]); return None
        old = S.song.book.active_id
        sheet_rt_save(sess)
        try:
            res = mutator(S.song)
        except SH.SheetError as ex:
            sess.rt.pop(old, None)
            toast(str(ex), C["amber"]); return None
        for k in list(sess.rt):
            if SH.sheet_of(S.song.book, k) is None:
                del sess.rt[k]
        if S.song.book.active_id != old:
            sheet_rt_load(sess)
            refresh_editor_for_sheet(sess)
        else:
            sess.rt.pop(old, None)
        if structural:
            mark_dirty()
        render_sheet_bar()
        return res

    def sheet_switch(sheet_id: str):
        sheet_change(lambda song: SH.switch(song, sheet_id), structural=False)

    def sheet_new(e=None):
        n = len(S.song.book.sheets) + 1 if S.song else 2
        sheet_change(lambda song: SH.new_blank(song, f"Sheet {n}", ""))

    def sheet_duplicate(sheet_id: str):
        sheet_change(lambda song: SH.duplicate(song, sheet_id))

    def sheet_close(sheet_id: str):
        sheet_change(lambda song: SH.close(song, sheet_id))

    def sheet_delete_confirm(sheet_id: str):
        sh = SH.sheet_of(S.song.book, sheet_id) if S.song else None
        if sh is None:
            return
        if len(S.song.book.sheets) <= 1:
            toast("A song needs at least one sheet. Create another sheet before deleting this one.", C["amber"]); return
        confirm("Delete sheet", f"Delete the sheet “{sh.name}” and its lyrics and options? This cannot be undone. Takes already generated from it are kept.", "Delete",
                lambda: sheet_change(lambda song: SH.delete(song, sheet_id)), danger=True)

    def sheet_rename_dialog(sheet_id: str):
        sh = SH.sheet_of(S.song.book, sheet_id) if S.song else None
        if sh is None:
            return
        tf = ft.TextField(label="Sheet name", value=sh.name, autofocus=True, **field_style(height=48))
        err = ft.Text("", size=11, color=C["red"])

        def do(e):
            try:
                name = SH.clean_name(tf.value)
            except SH.SheetError as ex:
                err.value = str(ex); safe_update(err); return
            page.pop_dialog()
            sheet_change(lambda song: SH.rename(song, sheet_id, name))
        page.show_dialog(ft.AlertDialog(title=ft.Text("Rename sheet"), content=ft.Column([tf, err], tight=True, spacing=4), bgcolor=C["panel"],
                                        actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.FilledButton("Rename", on_click=do)]))

    def render_sheet_bar():
        """The Excel-like tab bar under the lyrics. Active tab = accent; a dot marks a sheet with unsaved changes; the menu manages the sheet; + makes a new one."""
        if not S.song:
            sheet_tabs.content = None
            sheet_plus_holder.content = None
            safe_update(sheet_tabs, sheet_plus_holder)
            return
        sess = sessions.get(S.song.id)
        b = S.song.book
        tabs, ids = [], []
        for sid in b.open_ids:
            sh = SH.sheet_of(b, sid)
            if sh is None:
                continue
            ids.append(sid)
            row = [ft.Text(sh.name, size=12, weight=ft.FontWeight.W_600, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, tooltip=sh.name)]
            if sess is not None and sid in sess.dirty_sheets:
                row.append(ft.Text("●", size=10, color=C["amber"], tooltip="Unsaved changes in this sheet"))
            row.append(ft.PopupMenuButton(icon=ft.Icons.MORE_VERT_ROUNDED, icon_size=14, icon_color=C["text2"], tooltip="Sheet options", items=[
                ft.PopupMenuItem(content=ft.Text("Rename"), on_click=lambda e, i=sid: sheet_rename_dialog(i)),
                ft.PopupMenuItem(content=ft.Text("Duplicate"), on_click=lambda e, i=sid: sheet_duplicate(i)),
                ft.PopupMenuItem(content=ft.Text("Close tab (keeps the sheet)"), on_click=lambda e, i=sid: sheet_close(i)),
                ft.PopupMenuItem(content=ft.Text("Delete…"), on_click=lambda e, i=sid: sheet_delete_confirm(i))]))
            tabs.append(ft.Tab(label=ft.Row(row, spacing=4, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER)))
        sel = ids.index(b.active_id) if b.active_id in ids else 0

        def on_sheet_tab(e, ids=ids):
            try:
                i = int(e.control.selected_index)
            except Exception:
                return
            if 0 <= i < len(ids) and ids[i] != S.song.book.active_id:
                sheet_switch(ids[i])
        bar = ft.TabBar(tabs=tabs, scrollable=True, tab_alignment=ft.TabAlignment.START, indicator_color=C["accent"], label_color=C["text1"], unselected_label_color=C["text2"],
                        divider_height=0, indicator_thickness=3) if tabs else None
        sheet_tabs.content = ft.Tabs(length=len(tabs), selected_index=sel, on_change=on_sheet_tab, content=bar, expand=True) if tabs else None
        items = [ft.PopupMenuItem(content=ft.Text("New blank sheet"), on_click=sheet_new),
                 ft.PopupMenuItem(content=ft.Text("Duplicate this sheet"), on_click=lambda e: sheet_duplicate(b.active_id))]
        for h in SH.hidden(S.song):
            items.append(ft.PopupMenuItem(content=ft.Text(f"Open “{h.name}”"), on_click=lambda e, i=h.id: sheet_switch(i)))
        sheet_plus_holder.content = ft.PopupMenuButton(icon=ft.Icons.ADD_ROUNDED, icon_size=18, icon_color=C["accent"], tooltip="New sheet / reopen a closed sheet", items=items)
        safe_update(sheet_tabs, sheet_plus_holder)
        try:
            render_review()                                      # another sheet / song: its own review (or none)
            rebuild_chat()                                       # ... and the conversation of the open specialist on that sheet
        except NameError:
            pass

    def new_song(e=None):
        s = ST.create_song("Untitled Song", M.STARTER_LYRICS)
        refresh_lib()
        load_song_into_editor(s)
        set_tab("editor")

    def save_session(song_id: str) -> bool:
        """Save one open tab (active or not). False when it could not be saved: the caller must not close anything then."""
        if S.song and S.song.id == song_id:
            save_song_now()
            return not S.dirty
        sess = sessions.get(song_id)
        if sess is None:
            return True
        try:
            ST.save_song(sess.song)
        except Exception as ex:
            toast(f"Could not save “{sess.song.title}”: {ex}", C["red"]); return False
        sess.dirty = False
        sess.dirty_sheets.clear()
        refresh_lib(); rebuild_song_list(); rebuild_lib_page(); render_tabs()
        return True

    def drop_tab(song_id: str):
        """Remove a tab and its session (what was saved on disk stays). The nearest tab takes over, or My Music when none is left."""
        if song_id not in open_tabs:
            return
        idx = open_tabs.index(song_id)
        open_tabs.remove(song_id)
        was_active = bool(S.song and S.song.id == song_id)
        sessions.pop(song_id, None)
        if was_active:
            S.song, S.dirty = None, False
            if S.tab == "editor" and open_tabs:
                activate(open_tabs[min(idx, len(open_tabs) - 1)])
            else:
                clear_editor()
                if S.tab == "editor":
                    set_tab("library")
        render_tabs()

    def close_tab(song_id: str):
        if not is_dirty(song_id):
            drop_tab(song_id); return
        sess = sessions.get(song_id)
        title = (sess.song.title if sess else "") or "Untitled Song"

        def pick(act):
            page.pop_dialog()
            if act == "save" and not save_session(song_id):
                return                                          # a failed save never closes the tab
            if act == "discard":
                ST.discard_autosave(song_id)
            drop_tab(song_id)
        page.show_dialog(ft.AlertDialog(title=ft.Text("Unsaved Changes"), bgcolor=C["panel"], content=ft.Text(f'Save changes to "{title}"?'),
                                        actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.TextButton("Discard", on_click=lambda e: pick("discard")),
                                                 ft.FilledButton("Save", on_click=lambda e: pick("save"))]))

    def guard_all(then):
        """Before the program closes: every open tab with unsaved changes, not only the active one."""
        ids = [i for i in open_tabs if is_dirty(i) and i in sessions]
        if not ids:
            then(); return
        names = ", ".join((sessions[i].song.title or "Untitled Song") for i in ids)

        def pick(act):
            page.pop_dialog()
            if act == "save":
                for i in ids:
                    if not save_session(i):
                        return
            else:
                for i in ids:
                    ST.discard_autosave(i)
            then()
        page.show_dialog(ft.AlertDialog(title=ft.Text("Unsaved Changes"), bgcolor=C["panel"], content=ft.Text(f"{len(ids)} song{'s' if len(ids) != 1 else ''} with unsaved changes: {names}"),
                                        actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.TextButton("Discard", on_click=lambda e: pick("discard")),
                                                 ft.FilledButton("Save", on_click=lambda e: pick("save"))]))

    def render_tabs():
        """The Chrome-style tab bar: My Music (permanent), then one tab per open song, keyed by song id."""
        ctl = [ft.Container(content=ft.Row([ft.Icon(ft.Icons.HEADPHONES_ROUNDED, size=18, color="#FFFFFF" if S.tab == "library" else C["text2"]),
                                            ft.Text("My Music", size=14, weight=ft.FontWeight.W_600, color="#FFFFFF" if S.tab == "library" else C["text2"])], spacing=8),
                            padding=po(left=16, right=18, top=11, bottom=11), bgcolor=C["accent"] if S.tab == "library" else "#00000000", ink=True, border_radius=br(10),
                            on_click=go_library)]
        for sid in open_tabs:
            sess = sessions.get(sid)
            if not sess:
                continue
            on = S.tab == "editor" and bool(S.song) and S.song.id == sid
            title = sess.song.title or "Untitled Song"
            w = max(40, min(150, int(len(title) * 8.5) + 6))
            row = [ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, size=16, color="#FFFFFF" if on else C["text2"]),
                   ft.Container(ft.Text(title, size=14, weight=ft.FontWeight.W_600, color="#FFFFFF" if on else C["text2"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS), width=w)]
            if is_dirty(sid):
                row.append(ft.Text("●", size=12, color=C["amber"], tooltip="Unsaved Changes"))
            row.append(ft.IconButton(ft.Icons.CLOSE_ROUNDED, icon_size=14, width=24, height=24, padding=0, tooltip="Close Tab", icon_color="#FFFFFF" if on else C["text3"],
                                     on_click=lambda e, i=sid: close_tab(i)))
            ctl.append(ft.Container(content=ft.Row(row, spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER), tooltip=title, padding=po(left=12, right=4, top=5, bottom=5), ink=True,
                                    bgcolor=C["accent"] if on else C["panel2"], border_radius=br(10), on_click=lambda e, i=sid: activate(i)))
        tabs_row.controls = ctl
        safe_update(tabs_row)

    def save_song_now(e=None) -> bool:
        if not S.song:
            return False
        try:
            ST.save_song(S.song)
            S.dirty = False
            cs = sessions.get(S.song.id)
            if cs is not None:
                cs.dirty_sheets.clear()
            render_sheet_bar()
            dirty_txt.value = ""
            refresh_lib(); rebuild_song_list(); rebuild_lib_page(); render_tabs()
            safe_update(dirty_txt)
            if e is not None:
                toast("Saved", C["green"])
            return True
        except Exception as ex:
            toast(f"Could not save: {ex}", C["red"])
            return False

    def on_title_change(e):
        if _guard["set"] or not S.song:
            return
        S.song.title = title_tf.value or ""
        mark_dirty()
        render_tabs()
    title_tf.on_change = on_title_change

    def rename_dialog(song_id: str):
        entry = lib_entry(song_id)
        if not entry:
            return
        tf = ft.TextField(label="Song title", value=entry["song"].title, autofocus=True, **field_style(height=48))

        def do(e):
            name = (tf.value or "").strip()
            if not name:
                return
            page.pop_dialog()
            if S.queue_src == entry["song"].title:
                S.queue_src = name
            if S.song and S.song.id == song_id:
                S.song.title = name
                title_tf.value = name; safe_update(title_tf)
                save_song_now()
            else:
                s = ST.load_song(song_id); s.title = name; ST.save_song(s)
                if song_id in sessions:                          # an open tab keeps its draft; only its title follows
                    sessions[song_id].song.title = name
                refresh_lib(); rebuild_song_list(); rebuild_lib_page(); render_tabs()
        page.show_dialog(ft.AlertDialog(title=ft.Text("Rename"), content=tf, bgcolor=C["panel"], actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.FilledButton("Rename", on_click=do)]))

    def save_as_dialog(song_id: str | None = None):
        sid = song_id or (S.song.id if S.song else None)
        if not sid:
            return
        src = S.song if (S.song and S.song.id == sid) else ST.load_song(sid)
        tf = ft.TextField(label="Title of the copy", value=src.title + " (copy)", autofocus=True, **field_style(height=48))

        def do(e):
            name = (tf.value or "").strip() or "Untitled Song"
            page.pop_dialog()
            try:
                c = ST.duplicate_song(src, name)
                refresh_lib(); rebuild_song_list(); rebuild_lib_page()
                toast(f"Saved a copy: {c.title} (takes are not copied)", C["green"])
            except Exception as ex:
                toast(f"Could not save a copy: {ex}", C["red"])
        page.show_dialog(ft.AlertDialog(title=ft.Text("Save As"), content=tf, bgcolor=C["panel"], actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.FilledButton("Save copy", on_click=do)]))

    def delete_song_confirm(song_id: str):
        entry = lib_entry(song_id)
        title = entry["song"].title if entry else song_id
        n = len(entry["takes"]) if entry else 0

        def do():
            purge_queue(song_id=song_id)
            try:
                ST.delete_song(song_id)
            except Exception as ex:
                toast(f"Could not delete: {ex}", C["red"]); return
            refresh_lib()
            drop_tab(song_id)                                    # its tab (and any unsaved draft of a song that no longer exists) goes with it
            rebuild_song_list(); rebuild_lib_page()
        confirm("Delete song", f"Delete “{title}” with its lyrics, chat and {n} take(s)? This cannot be undone.", "Delete", do, danger=True)

    def clear_editor():
        _guard["set"] = True
        use_editor()
        title_tf.value = ""; lyrics_tf.value = ""
        _ui.update(epoch=_ui["epoch"] + 1, arrival=None, pending=None, irev=_ui["irev"] + 1, awaiting_echo=None, discarded_push=None)
        book.reset()
        _guard["set"] = False
        S.chat, S.takes, S.runs = [], [], []
        dirty_txt.value = ""; sel_chip.value = ""
        rebuild_output(); rebuild_chat(); build_settings(); update_generate_button()
        safe_update(title_tf, lyrics_tf, dirty_txt, sel_chip)
        refresh_tag_chip(); refresh_context(force=True)
        render_analysis()
        run_ms_hooks()

    # ═══ EDITOR: style / options ════════════════════════════════════════════════════════════════════════════
    settings_col = ft.Column(spacing=8, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    adv_tile = ft.ExpansionTile(title=ft.Text("Advanced Generation Settings", size=12, color=C["text2"]), controls=[], tile_padding=po(left=2, right=2), controls_padding=po(left=6, right=6, top=10, bottom=8),
                                expanded=bool(cfg.get("gen_adv_open", False)), on_change=lambda e: _adv_changed(e),
                                expanded_cross_axis_alignment=ft.CrossAxisAlignment.START, maintain_state=True, text_color=C["text2"])
    seed_field_ref = {"c": None}

    def sset(attr, cast=str, lo=None, hi=None):
        def h(e):
            if not S.song:
                return
            v = e.control.value
            try:
                if cast is not str:
                    v = cast(v)
                    if lo is not None: v = max(lo, v)
                    if hi is not None: v = min(hi, v)
            except (TypeError, ValueError):
                return
            if getattr(S.song.settings, attr) != v:
                setattr(S.song.settings, attr, v)
                mark_dirty()
        return h

    def refresh_workflows(wf_dd=None):
        files = CA.scan_workflows(AUDIO_API_DIR)
        names = [f.name for f in files]
        if S.song:
            s = S.song.settings
            before = (s.workflow, s.steps, s.cfg, dict(s.wf_params) if isinstance(s.wf_params, dict) else s.wf_params)
            notice = ""
            try:
                notice = QP.resolve_workflow(s, files)
            except QP.WorkflowError:
                if s.workflow not in names:
                    s.workflow = ""
            if (s.workflow, s.steps, s.cfg, s.wf_params) != before:
                mark_dirty()
            if notice:
                toast(notice, C["amber"])
        return names

    # style mode (Basic / Advanced) + prompt history
    prompt_tf = ft.TextField(label="Music Style Prompt", multiline=True, min_lines=5, max_lines=9,
                             hint_text="Describe the music style, for example: A cinematic alternative rock ballad with emotional male vocals, atmospheric guitars, powerful drums and a dramatic final chorus.",
                             **field_style(height=None, text_size=13, hint_style=ft.TextStyle(color=C["text3"], size=12)))
    hist_title = ft.Text("Prompt History", size=12, color=C["text2"])
    history_tile = ft.ExpansionTile(title=hist_title, controls=[], tile_padding=po(left=2, right=2), controls_padding=po(left=2, right=2, bottom=6), maintain_state=True,
                                    expanded_cross_axis_alignment=ft.CrossAxisAlignment.START, text_color=C["text2"])
    saved_btn = ft.OutlinedButton("Saved Styles (0)", icon=ft.Icons.BOOKMARKS_OUTLINED, height=38, tooltip="Styles you saved from AI Recommend: pick one to use it here")
    saved_ui = {"items": [], "list": None, "q": None}

    def saved_load(entry: dict, with_settings: bool):
        """Fills the Music Style Prompt with a saved caption (and, when asked, sets the genre / mood / BPM / key ... it was saved with). Nothing is generated."""
        if not S.song:
            return
        S.song.settings.style_prompt = entry.get("caption", "")
        prompt_tf.value = S.song.settings.style_prompt
        if with_settings and entry.get("settings"):
            try:
                SH.apply_settings(S.song, S.song.book.active_id, dict(entry["settings"]))
            except SH.SheetError as ex:
                toast(str(ex), C["amber"]); return
            build_settings()
        mark_dirty(); safe_update(prompt_tf)
        page.pop_dialog()
        toast(("Loaded the caption and settings of “%s”." if with_settings else "Loaded the caption of “%s”.") % entry.get("name", ""), C["green"])

    def saved_delete(entry: dict):
        try:
            ST.delete_saved_style(entry.get("id", ""))
        except Exception as ex:
            toast(f"Could not delete the saved style: {ex}", C["amber"]); return
        rebuild_saved()

    def saved_rows():
        q = ((saved_ui["q"].value if saved_ui["q"] is not None else "") or "").strip().lower()
        rows = []
        for h in saved_ui["items"]:
            cap = h.get("caption", "")
            hay = " ".join([h.get("name", ""), cap] + [str(v) for v in (h.get("settings") or {}).values()]).lower()
            if q and not all(w in hay for w in q.split()):
                continue
            sets = ", ".join(f"{k}: {v}" for k, v in (h.get("settings") or {}).items() if k in ("genre", "mood", "bpm", "keyscale"))
            rows.append(ft.Container(border_radius=8, bgcolor=C["field"], border=ft.Border.all(1, C["border"]), padding=po(left=10, right=4, top=6, bottom=6),
                                     content=ft.Row([ft.Container(ft.Column([ft.Text(h.get("name", ""), size=13, weight=ft.FontWeight.W_600, color=C["text1"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                                                             *([ft.Text(sets, size=10, color=C["accent"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)] if sets else []),
                                                                             ft.Text(cap, size=11, color=C["text3"], max_lines=3, overflow=ft.TextOverflow.ELLIPSIS)], spacing=2), expand=True, ink=True,
                                                                  on_click=lambda e, x=h: saved_load(x, False), tooltip=cap[:600] + "\n\nClick to load the caption into the input"),
                                                     *([ibtn(ft.Icons.TUNE_ROUNDED, "Load the caption AND its settings (genre, mood, BPM, key ...)", lambda e, x=h: saved_load(x, True), 18, w=34)] if h.get("settings") else []),
                                                     ibtn(ft.Icons.DELETE_OUTLINE, "Delete this saved style", lambda e, x=h: saved_delete(x), 18, w=34)], spacing=0,
                                                    vertical_alignment=ft.CrossAxisAlignment.CENTER)))
        return rows or [ft.Text("No saved style matches." if saved_ui["items"] else "Nothing yet. Press the bookmark on an AI Recommend card to keep a style here.", size=12, color=C["text3"])]

    def saved_refresh_list(e=None):
        if saved_ui["list"] is not None:
            saved_ui["list"].controls = saved_rows()
            safe_update(saved_ui["list"])

    def rebuild_saved():
        try:
            saved_ui["items"] = ST.load_saved_styles()
        except Exception as ex:
            saved_ui["items"] = []
            log.warning("saved styles could not be read: %s", ex)
        saved_btn.text = f"Saved Styles ({len(saved_ui['items'])})"
        safe_update(saved_btn)
        saved_refresh_list()

    def saved_open(e=None):
        rebuild_saved()
        saved_ui["q"] = ft.TextField(hint_text="Filter by name, caption, genre, mood ...", prefix_icon=ft.Icons.SEARCH_ROUNDED, autofocus=True, on_change=saved_refresh_list,
                                     **field_style(height=40, text_size=12, hint_style=ft.TextStyle(color=C["text3"], size=11)))
        saved_ui["list"] = ft.Column(saved_rows(), spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)

        def closed(e=None):
            saved_ui["list"] = saved_ui["q"] = None
            page.pop_dialog()
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Text("Saved Styles"),
                                        content=ft.Container(ft.Column([saved_ui["q"], saved_ui["list"]], spacing=10, tight=False), width=560, height=440),
                                        actions=[ft.TextButton("Close", on_click=closed)]))
    saved_btn.on_click = saved_open

    bb = {"obj": None}                                                                                      # the Basic Caption Builder (made on the first build_settings)
    basic_box = ft.Column(spacing=8, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)               # STRETCH: every field takes the whole width of the panel
    adv_box = ft.Column(spacing=8, visible=False, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    mode_row = ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def load_prompt_from_history(text: str):
        if not S.song:
            return
        S.song.settings.style_prompt = text                  # only fills the input: nothing is generated and no other history entry is touched
        prompt_tf.value = text
        mark_dirty(); safe_update(prompt_tf)

    def rebuild_history():
        items = S.song.settings.style_prompt_history if S.song else []
        hist_title.value = f"Prompt History ({len(items)})"
        rows = []
        for h in items:
            t = h.get("text", "")
            rows.append(ft.Container(border_radius=8, bgcolor=C["field"], border=ft.Border.all(1, C["border"]), padding=po(left=8, right=2, top=2, bottom=2),
                                     content=ft.Row([ft.Container(ft.Column([ft.Text(t, size=11, color=C["text1"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                                                                             ft.Text(h.get("ts", ""), size=10, color=C["text3"])], spacing=1), expand=True, ink=True,
                                                                  on_click=lambda e, x=t: load_prompt_from_history(x), tooltip=t[:600] + ("\n\nClick to load it into the input" if t else "")),
                                                     ibtn(ft.Icons.CONTENT_COPY_ROUNDED, "Copy", lambda e, x=t: page.run_task(copy_text, x, "Style prompt"), 15, w=28),
                                                     ibtn(ft.Icons.DELETE_OUTLINE, "Delete from history", lambda e, x=t: delete_history_item(x), 16, w=28)], spacing=0,
                                                    vertical_alignment=ft.CrossAxisAlignment.CENTER)))
        history_tile.controls = rows or [ft.Text("Nothing yet. Each prompt you generate with is saved here automatically.", size=11, color=C["text3"])]
        safe_update(hist_title, history_tile)

    def delete_history_item(text: str):
        if S.song and M.delete_prompt_history(S.song.settings, text):
            mark_dirty()
        rebuild_history()

    def on_prompt_change(e):
        if S.song and S.song.settings.style_prompt != (prompt_tf.value or ""):
            S.song.settings.style_prompt = prompt_tf.value or ""
            mark_dirty()
            try:
                render_basic_caption()                              # the Effective Caption card follows the prompt
            except NameError:
                pass
    prompt_tf.on_change = on_prompt_change

    # ═══ AI Music Director: the "AI Recommend" tab of the Style panel ═══════════════════════════════════════════
    # Analyze reads the open sheet into an immutable snapshot, asks the AI for three directions and only SHOWS them. Nothing is written to the sheet until Apply to Settings, which checks
    # that song, sheet, lyrics and settings are still the ones that were analyzed. The lyrics are never touched.
    DR_BG, DR_CARD, DR_SEL = "#0B1530", "#101E3D", "#16294F"
    ai_box = ft.Column(spacing=8, visible=False, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    dr = {"snap": None, "prop": None, "sel": 0, "fields": set(), "busy": False, "error": None, "undo": None, "cap_open": set(), "multi": []}
    dr_stale_txt = ft.Text("", size=11, color=C["amber"], visible=False, weight=ft.FontWeight.W_600)
    # The AI reads the lyrics (and this optional request) only. Its Basic / Advanced style settings are NOT sent unless the box below is ticked.
    dr_instr = ft.TextField(label="Your request to the AI (optional)", hint_text="e.g. sparse and acoustic, slower, more intimate", multiline=True, min_lines=1, max_lines=3,
                            **field_style(height=None, text_size=12, hint_style=ft.TextStyle(color=C["text3"], size=11)))
    dr_ref = ft.Checkbox(label="Use my current style settings as a reference", value=False, label_style=ft.TextStyle(size=11, color=C["text2"]),
                         tooltip="Off: the AI decides the style from the lyrics alone. On: it also sees your genre, mood, vocal, energy, extra tags and Music Style Prompt as a starting point.")

    dr_singer = dd("Who sings", [(k, label) for k, label, _v, _t in AD.SINGERS], "auto", None)
    singer_hook = {"fn": None}                                 # build_settings puts the real handler here; the dropdown gets its handler NOW, when it is created (a handler added after the control is shown is not delivered)
    dr_singer.on_select = synced(lambda e: singer_hook["fn"](e) if singer_hook["fn"] else None)
    dr_singer.tooltip = "Choose the singer(s) you want. The AI then writes all three directions for them and the Vocal setting follows. 'The AI decides' leaves it to the AI."

    def dr_ask_fn(backend: str, host: str):
        def ask(system, user):
            if backend == "grok":
                g = LC.grok_creds()
                return LC.ask_grok(g["key"], g["model"], system, user)
            return LC.ask_local(host, LLM_DIR / backend.split(":", 1)[1], system, user)
        return ask

    def dr_current():
        return S.song.id, S.song.book.active_id, lyr(), SH.settings_to_dict(S.song.settings)

    def dr_file(song_id: str) -> Path:
        return ST.song_dir(song_id) / "music_director.json"

    def dr_store(snap, prop):
        """The last analysis of THIS sheet: the snapshot it was made from and the AI's raw answer (parsed again when it is loaded). Replaced only by the next analysis."""
        try:
            f = dr_file(snap.song_id)
            d = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
            sd = asdict(snap)
            sd["settings"], sd["sections"] = [list(x) for x in snap.settings], list(snap.sections)
            d[snap.sheet_id] = {"snap": sd, "raw": prop.raw}
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            tmp.replace(f)
        except Exception as ex:
            log.warning("the AI Music Director result could not be saved: %r", ex)

    def dr_load(song_id: str, sheet_id: str):
        try:
            f = dr_file(song_id)
            e = json.loads(f.read_text(encoding="utf-8")).get(sheet_id) if f.exists() else None
            if not e:
                return None
            sd = dict(e["snap"])
            sd["settings"], sd["sections"] = tuple(tuple(x) for x in sd["settings"]), tuple(sd["sections"])
            snap = AD.Snapshot(**{k: v for k, v in sd.items() if k in AD.Snapshot.__dataclass_fields__})
            return snap, AD.finish(e["raw"], snap)
        except Exception as ex:
            log.warning("the saved AI Music Director result could not be read: %r", ex)
            return None

    def dr_sync_target():
        """Another song or sheet is open now: show ITS last analysis (or none). The result of the sheet that was left stays saved for when it comes back."""
        if dr["busy"] or not S.song:
            return
        key = (S.song.id, S.song.book.active_id)
        if dr["snap"] is not None and (dr["snap"].song_id, dr["snap"].sheet_id) == key:
            return
        got = dr_load(*key)
        if got is None:
            dr.update(snap=None, prop=None, sel=0, fields=set(), error=None, undo=None, multi=[])
        else:
            snap, prop = got
            dr.update(snap=snap, prop=prop, sel=0, error=None, undo=None, multi=[], fields=AD.default_selection(snap, prop, prop.directions[0]))
            dr_instr.value, dr_ref.value, dr_singer.value = snap.instructions, bool(snap.reference), snap.singer or "auto"       # what the analysis was asked with

    def dr_stale() -> str:
        if dr["snap"] is None:
            return ""
        if not S.song:
            return "No song is open — Reanalyze."
        sid, sh, ly, st = dr_current()
        return AD.stale_reason(dr["snap"], sid, sh, ly, st)

    def dr_wrong_target() -> str:
        """Only another song or sheet locks the cards; edited lyrics only show the 'Reanalyze' note (the style is still the user's to use)."""
        if dr["snap"] is None or not S.song:
            return "No song is open — Reanalyze." if dr["snap"] is not None else ""
        return AD.wrong_target_reason(dr["snap"], S.song.id, S.song.book.active_id)

    def dr_tick():
        """Called whenever the song changes: the cards are drawn again (locked, with 'Lyrics changed — Reanalyze') as soon as the draft is out of date."""
        if style_tab["v"] != "ai":
            return
        dr_sync_target()
        if dr["prop"] is None:
            return
        if dr_stale() != dr_stale_txt.value:
            dr_render()
    dr_hooks.append(dr_tick)

    def dr_show(name: str, v) -> str:
        if name == "vocal":
            return dict(VOCALS).get(v, v) or "Auto"
        if name == "energy":
            return dict(ENERGIES).get(v, v) or "Auto"
        if name == "mood":
            return v or "None"
        if name == "timesignature":
            return dict(TIMESIGS).get(str(v), str(v))
        if name == "duration":
            return f"{v}s"
        if name == "language":
            return "Auto" if v == "auto" else f"{dict(LANGS).get(v, v)} ({v})"
        return str(v)

    def dr_start(e=None):
        if dr["busy"]:
            return
        if not S.song:
            toast("Open or create a song first.", C["amber"]); return
        if S.chat_busy_song or S.an_busy or tp["busy"]:
            toast("The AI is still busy. Try again in a moment.", C["amber"]); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        if not lyr().strip():
            toast("Write some lyrics first: the AI Music Director reads them.", C["amber"]); return
        sid, sh, ly, st = dr_current()
        snap = AD.make_snapshot(sid, sh, SH.active_sheet(S.song).name, S.song.title, ly, st, instructions=dr_instr.value or "", reference=bool(dr_ref.value), singer=dr_singer.value or "")
        page.run_task(dr_run, snap, S.backend, S.host)

    async def dr_run(snap, backend: str, host: str):
        loop = asyncio.get_running_loop()
        dr["run"] = token = dr.get("run", 0) + 1
        dr.update(busy=True, error=None, t0=time.time())
        S.chat_busy_song = snap.song_id
        dr_render()
        page.run_task(dr_clock, token)
        prop, err = None, None
        try:
            prop = await loop.run_in_executor(None, lambda: (LC.set_call_tag("director"), AD.analyze(dr_ask_fn(backend, host), snap, "Thai" if I18N.lang() == "th" else "English", trace=lambda e: TRC.log_event("recommend", backend=backend, **e)))[1])     # one question, no automatic retry
        except AD.DirectorError as ex:
            err = ex
            log.debug("AI Music Director refused the answer: %s\nRAW:\n%s", ex.problems, ex.raw)
        except Exception as ex:
            err = AD.DirectorError(f"AI error: {ex}")
        finally:
            if dr.get("run") == token:
                S.chat_busy_song = ""
        took = time.time() - dr.get("t0", time.time())
        if err is not None or took > 45:                         # the log keeps the slow or failed analyses (nothing else records them)
            log.warning("AI Music Director: %s after %.0f s (backend %s, %d chars of lyrics)%s", "failed" if err is not None else "slow answer", took, backend, len(snap.lyrics),
                        f": {err.problems[:2] if isinstance(err, AD.DirectorError) else err}" if err is not None else "")
        if dr.get("run") != token:
            return                                              # cancelled by the user: this answer is ignored
        dr["busy"] = False
        if err is not None:
            dr["error"] = err                                   # the previous draft (if any) stays; the failed raw answer is kept for the debug view
        else:
            dr.update(snap=snap, prop=prop, sel=0, error=None, multi=[], fields=AD.default_selection(snap, prop, prop.directions[0]))
            try:
                dr_store(snap, prop)
            except Exception as ex:                             # saving must never keep the panel waiting
                log.warning("the AI Music Director result could not be stored: %r", ex)
        dr_render()

    def dr_use(i: int):
        """A click on a direction card chooses it: its caption and the settings the AI gave (except the ones the user has chosen otherwise) are written to the song at once. Nothing is
        generated and the lyrics are not touched; Restore Previous Settings (beside Generate) undoes it."""
        if dr["prop"] is None or dr["busy"] or not S.song:
            return
        if dr_wrong_target():
            dr_tick(); return
        dr["sel"] = i
        dr["fields"] = AD.default_selection(dr_snap_now(), dr["prop"], dr["prop"].directions[i])
        if dr["multi"]:
            dr["multi"] = []                                     # a plain click goes back to "use one style"
            update_generate_button()
        dr_apply()

    def ctrl_down() -> bool:
        """Is Ctrl held right now? Asked from Windows itself at the moment of the click (a Flet click event does not carry the modifier keys)."""
        try:
            import ctypes
            return bool(ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000)
        except Exception:
            return False

    def dr_click(i: int):
        dr_toggle_multi(i) if ctrl_down() else dr_use(i)

    def dr_toggle_multi(i: int):
        """Ctrl+click: pick (or drop) a card for the queue. Nothing is written to the song: Generate then queues one song per picked card, each with that card's own settings."""
        if dr["prop"] is None or dr["busy"] or not S.song:
            return
        if dr_wrong_target():
            dr_tick(); return
        if not dr["multi"]:                                      # the card shown "Applied" (chosen by a plain click) is the first pick; Ctrl+click adds to it
            u = dr["undo"]
            if u and u["song"] == S.song.id and u.get("index", -1) != i and 0 <= u.get("index", -1) < len(dr["prop"].directions):
                dr["multi"] = [u["index"]]
        dr["multi"] = [x for x in dr["multi"] if x != i] if i in dr["multi"] else dr["multi"] + [i]
        dr_render()
        update_generate_button()

    def dr_clear_multi(e=None):
        dr["multi"] = []
        dr_render()
        update_generate_button()

    def dr_card_settings(i: int) -> dict:
        """The settings one card puts on ITS song: the caption and every value the AI gave for the card (BPM, key, time signature, genre, mood, vocal, energy, duration). The song's Language stays as
        the user has it. Raises AD.ApplyError (a sentence for the user)."""
        d = dr["prop"].directions[i]
        sug = AD.suggested(dr["prop"], d)
        sid, sh, ly, st = dr_current()
        fields = {n for n in sug if n != "language"}
        return AD.plan_apply(dr["snap"], dr["prop"], d, fields, sid, sh, ly, st).changes

    def dr_multi_cards():
        """The picked cards as queue requests [{"name", "changes"}]; [] when none is picked; None when one cannot be used (the reason was shown)."""
        if not dr["multi"] or dr["prop"] is None or not S.song:
            return []
        if dr_wrong_target():
            dr_tick(); return None
        out = []
        for i in sorted(dr["multi"]):
            if i >= len(dr["prop"].directions):
                continue
            try:
                out.append({"name": dr["prop"].directions[i].name, "changes": dr_card_settings(i)})
            except AD.ApplyError as ex:
                toast(f"Nothing was queued. {ex}", C["amber"]); return None
        return out

    def dr_customize(i: int):
        """Customize: tick the fields to write for this card, then apply only those."""
        d = dr["prop"].directions[i]
        sug = AD.suggested(dr["prop"], d)
        now = dr_snap_now()
        sel = set(AD.default_selection(now, dr["prop"], d))
        rows = []

        def tick(e, n):
            sel.add(n) if e.control.value else sel.discard(n)
        for n in ("style_prompt", "genre", "mood", "vocal", "energy", "bpm", "keyscale", "timesignature", "duration", "language"):
            if n not in sug:
                continue
            mine = AD.user_value(now, n)
            if n != "style_prompt" and dr_show(n, mine) == dr_show(n, sug[n]):
                sel.discard(n)
                continue                                         # already the same: nothing to choose
            cb = ft.Checkbox(value=n in sel, on_change=lambda e, n=n: tick(e, n))
            if n == "style_prompt":
                head, sub = "Music Caption", ft.Text("The style mode becomes Advanced", size=11, color=C["text3"])
            else:
                head = AD.LABELS.get(n, n)
                sub = ft.Row([ft.Text(str(dr_show(n, mine)) or "-", size=12, color=C["text3"]), ft.Icon(ft.Icons.ARROW_FORWARD_ROUNDED, size=14, color=C["text3"]),
                              ft.Text(str(dr_show(n, sug[n])), size=12, weight=ft.FontWeight.W_600, color=C["accent"])], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            rows.append(ft.Container(ft.Row([cb, ft.Column([ft.Text(head, size=13, weight=ft.FontWeight.W_600, color=C["text1"]), sub], spacing=2, tight=True, expand=True)],
                                             spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                                     bgcolor=C["panel2"], border_radius=10, padding=po(8, 8, 12, 8), border=ft.Border.all(1, C["border"])))

        busy = {"on": False}

        def go(e):
            if busy["on"]:
                return                                           # a second quick click: not applied twice
            busy["on"] = True
            dr["sel"], dr["fields"] = i, set(sel)
            try:
                if dr_apply():                                   # True only when the settings were really applied; a refusal leaves the dialog open with its message
                    page.pop_dialog()
            finally:
                busy["on"] = False
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Row([ft.Icon(ft.Icons.TUNE_ROUNDED, size=20, color=C["accent"]), ft.Text(f"Customize Apply — {d.name}", size=16, weight=ft.FontWeight.BOLD, expand=True)], spacing=8),
                                        content=ft.Container(ft.Column([ft.Text("Choose what to write to the song", size=11, color=C["text3"])] + rows, spacing=6, tight=True), width=440), actions_padding=po(16, 4, 16, 14),
                                        actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                                                 ft.FilledButton("Apply Selected", on_click=go, style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))]))

    def dr_rebase(song_id, sheet_id, lyrics):
        old = dr["snap"]
        dr["snap"] = AD.make_snapshot(song_id, sheet_id, SH.active_sheet(S.song).name, S.song.title, lyrics, SH.settings_to_dict(S.song.settings),
                                      instructions=old.instructions if old else "", reference=old.reference if old else False, singer=old.singer if old else "")

    def dr_snap_now():
        """The analyzed snapshot with the settings the user has NOW: Apply never overwrites a value the user chose after the analysis (Basic changes do not make the draft stale)."""
        return AD.refresh_settings(dr["snap"], dr_current()[3])

    def dr_apply(e=None):
        if dr["prop"] is None or not S.song:
            return False
        d = dr["prop"].directions[dr["sel"]]
        sid, sh, ly, st = dr_current()
        try:
            plan = AD.plan_apply(dr["snap"], dr["prop"], d, set(dr["fields"]), sid, sh, ly, st)
        except AD.ApplyError as ex:
            toast(f"Nothing was applied. {ex}", C["amber"]); dr_tick(); return False
        try:
            SH.apply_settings(S.song, sh, plan.changes)         # the analyzed sheet, by id; never the lyrics
        except SH.SheetError as ex:
            toast(str(ex), C["amber"]); return False
        dr["undo"] = {"song": sid, "sheet": sh, "previous": plan.previous, "applied": dict(plan.changes), "name": d.name, "index": dr["sel"]}
        dr_rebase(sid, sh, ly)                                   # the draft stays usable: it now describes the sheet as applied
        dr["fields"] = AD.default_selection(dr["snap"], dr["prop"], d)
        mark_dirty(); build_settings()
        toast(f"Applied “{d.name}”: Style mode is now Advanced. Generate uses this caption. Restore Previous Settings (beside Generate) undoes it.", C["green"])
        return True

    def dr_restore(e=None):
        u = dr["undo"]
        if not u or not S.song or S.song.id != u["song"]:
            toast("The song changed. Nothing was restored.", C["amber"]); return
        sheet = SH.sheet_of(S.song.book, u["sheet"]) if S.song.book.active_id != u["sheet"] else None
        if S.song.book.active_id != u["sheet"] and sheet is None:
            toast("That sheet no longer exists. Nothing was restored.", C["amber"]); return
        now = SH.settings_to_dict(S.song.settings) if S.song.book.active_id == u["sheet"] else dict(sheet.settings)
        back, kept = AD.plan_restore(u["previous"], u.get("applied", u["previous"]), now)       # what the user changed after Apply is not overwritten
        try:
            if back:
                SH.apply_settings(S.song, u["sheet"], back)
        except SH.SheetError as ex:
            toast(str(ex), C["amber"]); return
        dr["undo"] = None
        if S.song.book.active_id == u["sheet"]:
            dr_rebase(S.song.id, u["sheet"], lyr())
        mark_dirty(); build_settings()
        if kept:
            toast("Restored what Apply had changed. Kept your later changes: " + ", ".join(AD.LABELS.get(k, k) for k in kept) + ".", C["amber"])
        else:
            toast("Previous settings restored.", C["green"])

    def dr_save(i: int):
        """Keeps a direction (caption + the valid genre / mood / vocal / energy / BPM / key / time signature the AI gave) in the Saved Styles. Nothing in the song changes. Works on a card whose
        lyrics have changed too: a saved style is a style, not an analysis of these lyrics."""
        if dr["prop"] is None:
            return
        d = dr["prop"].directions[i]
        sug = AD.suggested(dr["prop"], d)
        vals = {}
        for n in ST.STYLE_FIELDS:
            if sug.get(n) in (None, ""):
                continue
            v, bad = AD.check_value(n, sug[n])
            if not bad and v is not None:
                vals[n] = v
        try:
            new = ST.add_saved_style(d.name, d.caption, vals)
        except Exception as ex:
            toast(f"Could not save the style: {ex}", C["amber"]); return
        rebuild_saved()
        toast(f"Saved “{d.name}”. Find it in Advanced → Saved Styles." if new else f"“{d.name}” was already saved: updated.", C["green"])

    def dr_head(t: str):
        return ft.Text(t, size=11, weight=ft.FontWeight.BOLD, color=C["text3"])

    def dr_chip(label: str, value: str):
        return ft.Container(ft.Text(f"{label}: {value}", size=10, color=C["text1"], no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS), padding=po(8, 3, 8, 3), border_radius=11, bgcolor=C["field"])

    def dr_toggle_caption(n: int):
        dr["cap_open"] ^= {n}
        dr_render()

    def dr_card(d, locked: bool):
        i = d.index - 1
        sug = AD.suggested(dr["prop"], d)
        u = dr["undo"]
        applied = bool(u and S.song and u["song"] == S.song.id and u["name"] == d.name and u.get("index", i) == i) and not dr["multi"]      # while picking for the queue only the picked cards are marked: the count starts fresh
        picked = i in dr["multi"]
        chips = [dr_chip(lbl, dr_show(n, sug[n])) for n, lbl in (("genre", "Style"), ("mood", "Mood"), ("vocal", "Vocal"), ("energy", "Energy"), ("bpm", "BPM"), ("keyscale", "Start key"), ("timesignature", "Time sig.")) if sug.get(n) not in (None, "")]
        cap_open = d.index in dr["cap_open"]
        notes = [ft.Text(f"⚠ {AD.LABELS.get(f, f)}: the AI wrote “{str(raw)[:40]}” ({why_}); it is not applied.", size=10, color=C["amber"]) for f, raw, why_ in AD.problems_of(dr["prop"], d) if f not in AD.STYLE_WORD_FIELDS][:2]       # Genre / Mood / Energy / Vocal words are all in the caption that is applied (AD.full_caption), so nothing is "not applied" and nothing is said; the caption remarks (BPM mentioned, cue vs caption) are not shown on the card
        hint = "Picked for the queue (Ctrl+click again to drop it)" if picked else ("Applied. Undo is beside Generate." if applied else "Click to use this style")
        foot = ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED if (applied or picked) else ft.Icons.TOUCH_APP_OUTLINED, size=14, color=C["green"] if (applied or picked) else C["text3"]),
                       ft.Text(hint, size=10, color=C["green"] if (applied or picked) else C["text3"], expand=True),
                       ibtn(ft.Icons.BOOKMARK_ADD_OUTLINED, "Save this style (its caption and settings) to Saved Styles: open it later in Advanced mode, in any song", lambda e, i=i: dr_save(i), 18, w=34),
                       ft.TextButton("Customize", disabled=locked, on_click=lambda e, i=i: dr_customize(i), tooltip="Choose which of these values to apply")],
                      spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        return ft.Container(ft.Column([
            ft.Row([ft.Container(ft.Text(str(d.index), size=11, weight=ft.FontWeight.BOLD, color="#FFFFFF"), width=20, height=20, border_radius=10,
                                 bgcolor=C["green"] if (applied or picked) else C["accent"], alignment=ft.Alignment(0, 0)),
                    ft.Text(d.name, size=13, weight=ft.FontWeight.W_700, color=C["text1"], expand=True)], spacing=8),
            ft.Text(d.reason, size=11, color=C["text2"]),
            *([ft.Text(d.characteristics, size=11, color=C["text3"], italic=True)] if d.characteristics else []),
            ft.Text(d.caption, size=11 if cap_open else 10, color=C["text2"] if cap_open else C["text3"], max_lines=None if cap_open else 3, overflow=ft.TextOverflow.VISIBLE if cap_open else ft.TextOverflow.ELLIPSIS, selectable=cap_open),
            ft.TextButton("Hide full caption" if cap_open else "Show full caption", on_click=lambda e, n=d.index: dr_toggle_caption(n), style=ft.ButtonStyle(padding=po(0, 0, 0, 0)), height=22),
            ft.Row(chips, wrap=True, spacing=6, run_spacing=4), *notes, foot], spacing=4),
            padding=10, border_radius=10, ink=not locked, bgcolor=DR_SEL if (applied or picked) else DR_CARD, border=ft.Border.all(2 if picked else 1, C["green"] if (applied or picked) else C["border"]), opacity=0.6 if locked else 1,
            on_click=None if locked else (lambda e, i=i: dr_click(i)),
            tooltip="Click to use this style: its caption and settings are put into the song. Nothing is generated and the lyrics are not changed; Undo is beside Generate. Ctrl+click picks several styles: Generate then adds one song per picked style to the queue.")

    dr_btn = {"b": None, "blink": False}

    async def dr_clock(token: int):
        """While the AI works: the button counts the seconds, so a slow answer is not mistaken for a hang."""
        while dr["busy"] and dr.get("run") == token:
            b = dr_btn["b"]
            if b is not None:
                b.content = f"Analyzing… {int(time.time() - dr.get('t0', time.time()))} s"
                safe_update(b)
            await asyncio.sleep(1)

    def dr_cancel(e=None):
        """Stop waiting: the answer, if it still arrives, is ignored and the previous analysis stays."""
        if not dr["busy"]:
            return
        dr["run"] = dr.get("run", 0) + 1
        dr["busy"] = False
        S.chat_busy_song = ""
        dr_render()
        toast("Stopped waiting for the AI. The previous analysis is kept.", C["amber"])

    async def dr_blink():
        """While the shown analysis is out of date (the lyrics or the length / language changed) the Analyze button pulses; nothing is cleared."""
        on = False
        try:
            while dr["prop"] is not None and not dr["busy"] and style_tab["v"] == "ai" and dr_stale():
                b = dr_btn["b"]
                if b is not None:
                    on = not on
                    b.style = ft.ButtonStyle(bgcolor=C["amber"] if on else C["accent"], color="#FFFFFF")
                    safe_update(b)
                await asyncio.sleep(0.7)
        finally:
            dr_btn["blink"] = False
            b = dr_btn["b"]
            if b is not None:
                b.style = ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF")
                safe_update(b)

    def dr_render():
        dr_sync_target()
        ctl = [ft.Row([ft.Text("✨ AI Music Director", size=14, weight=ft.FontWeight.W_700, color=C["text1"], expand=True),
                       ft.ProgressRing(width=16, height=16, stroke_width=2, visible=dr["busy"])], vertical_alignment=ft.CrossAxisAlignment.CENTER)]
        ctl.append(ft.Text("Reads your lyrics like a producer and suggests three music directions of its own. It does not see your Basic or Advanced style settings unless you tick the box. Your lyrics and cues are never changed.",
                           size=11, color=C["text3"]))
        dr_instr.disabled = dr_ref.disabled = dr_singer.disabled = dr["busy"]
        ctl += [dr_instr, dr_singer, dr_ref]
        dr_btn["b"] = ft.FilledButton("Analyzing…" if dr["busy"] else "Analyze Lyrics", icon=ft.Icons.AUTO_AWESOME_ROUNDED, disabled=dr["busy"], on_click=dr_start, height=38,
                                      style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))
        ctl.append(ft.Row([ft.Container(dr_btn["b"], expand=True)] + ([ft.OutlinedButton("Cancel", height=38, on_click=dr_cancel)] if dr["busy"] else []), spacing=6))
        err = dr["error"]
        if err is not None:
            ctl.append(ft.Container(ft.Column([ft.Text("The AI answer could not be used. Nothing was changed.", size=12, weight=ft.FontWeight.W_600, color=C["red"]),
                                               *[ft.Text("• " + p, size=11, color=C["text2"]) for p in err.problems[:6]],
                                               *([ft.Container(ft.Column([ft.Text(err.raw, size=10, color=C["text3"], font_family="Consolas", selectable=True)], scroll=ft.ScrollMode.AUTO), height=120,
                                                               bgcolor=C["field"], border_radius=6, padding=6),
                                                  ft.TextButton("Copy raw answer", on_click=lambda e: page.run_task(copy_text, err.raw, "Raw AI answer"))] if err.raw else [])], spacing=4),
                                    padding=10, border_radius=10, bgcolor=DR_CARD, border=ft.Border.all(1, C["red"])))
        prop = dr["prop"]
        if prop is None:
            ai_box.controls = ctl
            safe_update(ai_box)
            dr_side_buttons()
            return
        why = dr_stale()
        dr_stale_txt.value, dr_stale_txt.visible = why, bool(why)
        ctl.append(dr_stale_txt)
        if why and not dr["busy"] and not dr_btn["blink"]:
            dr_btn["blink"] = True
            page.run_task(dr_blink)
        if prop.analysis:                                       # only a saved V1 answer has the analysis report; V2 puts the decision in each Reason
            ctl.append(dr_head("EMOTIONAL ANALYSIS"))
            AN_ICON = {"emotion": ft.Icons.FAVORITE_BORDER_ROUNDED, "theme": ft.Icons.LIGHTBULB_OUTLINE_ROUNDED, "narrative": ft.Icons.AUTO_STORIES_OUTLINED, "energy arc": ft.Icons.SHOW_CHART_ROUNDED, "vocal character": ft.Icons.MIC_NONE_ROUNDED}
            ctl.append(ft.Container(ft.Column([ft.Column([ft.Row([ft.Icon(AN_ICON.get(k.lower(), ft.Icons.CIRCLE_OUTLINED), size=14, color=C["accent"]),
                                                                  ft.Text(k.title(), size=12, weight=ft.FontWeight.W_700, color=C["text1"])], spacing=6),
                                                          ft.Text(v, size=12.5, color=C["text1"], selectable=True)], spacing=3) for k, v in prop.analysis.items()], spacing=12),
                                    padding=po(14, 12, 14, 12), border_radius=10, bgcolor=DR_BG, border=ft.Border.all(1, C["border"])))
        ctl.append(dr_head("AI DIRECTIONS  (click a style to use it; Ctrl+click picks several)"))
        locked = bool(dr_wrong_target()) or dr["busy"]
        ctl += [dr_card(x, locked) for x in prop.directions]
        if dr["multi"]:
            ctl.append(ft.Container(ft.Row([ft.Icon(ft.Icons.PLAYLIST_ADD_CHECK_ROUNDED, size=18, color=C["green"]),
                                            ft.Text(f"{len(dr['multi'])} styles picked: Generate adds one song per style to the queue, each with its own BPM, key and time signature. The song's own settings are not changed.",
                                                    size=11, color=C["text1"], expand=True),
                                            ft.TextButton("Clear picks", on_click=dr_clear_multi)], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                                    padding=po(10, 6, 6, 6), border_radius=10, bgcolor=DR_BG, border=ft.Border.all(1, C["green"])))
        ai_box.controls = ctl
        safe_update(ai_box)
        dr_side_buttons()

    def dr_side_buttons():
        """Regenerate / Restore are small icon buttons beside Generate. They appear only on the AI Recommend tab, and only when there is a draft (Restore: after a style was used)."""
        on = style_tab["v"] == "ai" and rp["page"] == "style" and dr["prop"] is not None
        dr_regen_btn.visible = on
        dr_regen_btn.disabled = dr["busy"]
        dr_restore_btn.visible = on and bool(dr["undo"])
        dr_restore_btn.tooltip = f"Restore Previous Settings (before “{dr['undo']['name']}”)" if dr["undo"] else "Restore Previous Settings"
        safe_update(dr_regen_btn, dr_restore_btn)

    def _dr_icon(icon, tip, fn, bg=None):
        return ft.IconButton(icon, icon_size=22, width=46, height=46, padding=0, tooltip=tip, on_click=fn, visible=False, icon_color="#FFFFFF" if bg else C["text1"], bgcolor=bg or C["panel2"],
                             style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)))
    dr_regen_btn = _dr_icon(ft.Icons.REFRESH_ROUNDED, "Regenerate Suggestions (analyze the lyrics again)", dr_start)
    dr_restore_btn = _dr_icon(ft.Icons.UNDO_ROUNDED, "Restore Previous Settings", dr_restore)

    shared_box = ft.Column(spacing=8, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)                        # Model, BPM, Key, Time, Language, Duration, Seed: shown on the Basic and Advanced tabs
    source_note = ft.Text("", size=11, color=C["text3"])
    style_tab = {"v": "basic"}
    rp = {"page": "menu", "until": 0.0, "tab": "tools"}                     # the Right Panel's own navigation: the page shown ("menu", "syl", "rhyme", "rhythm", "thes", "style") and the end of the running slide
    side_hooks: list = []                                 # called when the sidebar width changes (apply_widths)
    style_box_ref = {"c": None}

    def show_style_tab(t: str):
        """Basic / Advanced / AI Recommend are three views of the Style panel. Only Basic and Advanced are a style_mode; AI Recommend is just a view and never changes it."""
        style_tab["v"] = t
        ai = t == "ai"
        if not ai and dr["multi"]:
            dr["multi"] = []                                   # the picks belong to the AI Recommend view: leaving it puts Generate back to a single song
            update_generate_button()
        basic_box.visible = t == "basic"
        adv_box.visible = t == "advanced"
        shared_box.visible = True                              # the same shared generation settings in every mode: one set of controls, one state (SongSettings)
        adv_tile.visible = True
        ai_box.visible = ai
        for key, btn in mode_buttons.items():
            on = key == t
            btn.bgcolor = C["accent"] if on else "#00000000"
            btn.content.color = "#FFFFFF" if on else C["text2"]
            btn.content.weight = ft.FontWeight.W_600 if on else ft.FontWeight.NORMAL
        mode = S.song.settings.style_mode if S.song else "basic"
        source_note.value = {"basic": "The caption is built from the chips you choose (no AI).", "advanced": "Write and control the music caption yourself.",
                             "ai": "Let AI suggest musical directions for your lyrics."}[t]
        source_note.tooltip = ("Active Caption Source: Advanced. The Music Style Prompt is sent as written; the Basic tags are kept but not sent." if mode == "advanced"
                               else "Active Caption Source: Basic. The caption is composed from your chips and notes; no AI writes it. The Advanced prompt is kept but not sent.")
        dr_side_buttons()
        safe_update(basic_box, adv_box, shared_box, adv_tile, ai_box, source_note, *mode_buttons.values())
        try:
            render_basic_caption()
            rp_refresh_header()
        except NameError:
            pass                                              # called while the window is still being built
        if ai:
            dr_render()

    def set_style_mode(m: str, mark: bool = True):
        if not S.song:
            return
        S.song.settings.style_mode = m                       # both modes' data stay in the song; only the visible one is sent
        show_style_tab(m)
        if mark:
            mark_dirty()

    def pick_tab(k: str):
        if k == "ai":
            show_style_tab("ai")
        else:
            set_style_mode(k)

    mode_buttons = {k: ft.Container(content=ft.Text(lbl, size=12, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, text_align=ft.TextAlign.CENTER), padding=po(left=6, right=6, top=7, bottom=7),
                                    border_radius=6, ink=True, expand={"basic": 3, "advanced": 4, "ai": 6}[k], alignment=ft.Alignment(0, 0), on_click=lambda e, k=k: pick_tab(k))
                    for k, lbl in (("basic", "Basic"), ("advanced", "Advanced"), ("ai", "✨ AI Recommend"))}
    mode_row.visible = False                                                # Basic / Advanced / AI Recommend are chosen in the Generate tab (navigation), not by this row
    mode_row.controls = [ft.Container(ft.Row(list(mode_buttons.values()), spacing=0), bgcolor=C["field"], border_radius=8, border=ft.Border.all(1, C["border"]), expand=True)]

    def ui_card(icon, title: str, body: list):
        """A titled card of the Style panel: a small header, then its controls."""
        head = ft.Row([ft.Icon(icon, size=15, color=C["text2"]), ft.Text(title, size=13, weight=ft.FontWeight.W_600, color=C["text1"])], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        return ft.Container(ft.Column([head, *body], spacing=8, horizontal_alignment=ft.CrossAxisAlignment.STRETCH), padding=10, border_radius=10, bgcolor=C["panel2"], border=ft.Border.all(1, C["border"]))

    meta_ctl = {"f": None}
    meta_body = ft.Column(spacing=8, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    def side_inner_w() -> float:
        """The width the fields of the Style panel really have: the sidebar (or the drawer) minus the panel and card paddings."""
        try:
            w = drawer_layer.width if (layout["drawer"] and drawer_layer.width) else (sidebar_holder.width or S.side_w)
        except NameError:
            w = S.side_w
        return float(w) - 24 - 20 - 8

    def layout_meta():
        """BPM, Key, Time / Duration, Language, Seed: three columns when the sidebar is wide, two when it is narrow (nothing is cut, nothing overflows)."""
        f = meta_ctl["f"]
        if not f:
            return
        three = side_inner_w() >= 370
        if meta_ctl.get("sig") == (three, id(f)) and meta_body.controls:
            return                                                  # same shape and the same fields: nothing to rebuild while a divider moves
        meta_ctl["sig"] = (three, id(f))
        rows = [[f["bpm"], f["key"], f["time"]], [f["dur"], f["lang"], f["seed"]]] if three else [[f["bpm"], f["key"]], [f["time"], f["dur"]], [f["lang"], f["seed"]]]
        meta_body.controls = [ft.Row(r, spacing=8, vertical_alignment=ft.CrossAxisAlignment.START) for r in rows]
        safe_update(meta_body)
    side_hooks.append(layout_meta)

    cover_ui = {"refresh": None}

    def _cover_lists_hook():
        fn = cover_ui.get("refresh")
        if fn:
            fn()
    ms_hooks.append(_cover_lists_hook)

    def build_settings():
        if not S.song:
            settings_col.controls = [ft.Text("Open or create a song", size=12, color=C["text3"])]
            adv_tile.controls = []
            shared_box.controls = []
            cover_ui["refresh"] = None
            safe_update(settings_col, adv_tile, shared_box)
            return
        s = S.song.settings
        names = refresh_workflows()
        def on_workflow(e):
            """Another workflow: its own steps / CFG come back (the old ones are put away), and the quality row is drawn again for it."""
            if not S.song or (e.control.value or "") == S.song.settings.workflow:
                return
            QP.switch_workflow(S.song.settings, S.song.settings.workflow, e.control.value or "", QP.family_of_file(AUDIO_API_DIR / (e.control.value or "")))
            S.song.settings.workflow = e.control.value or ""
            mark_dirty()
            build_settings()
        wf_dd = dd("Model", [(n, "ACE-Step 1.5 (ComfyUI)" if n == names[0] and len(names) == 1 else n) for n in names] or [("", "No workflow found")], s.workflow or "", on_workflow, None)
        wf_dd.expand = True
        fam = QP.family_of_file(AUDIO_API_DIR / s.workflow) if s.workflow else QP.DISTILLED
        q_hint = ft.Text("", size=10, color=C["text3"])
        q_label = {"draft": "Draft", "standard": "Standard", "high": "High", "custom": "Custom"}
        q_dd = dd("Quality", [(k, q_label[k] + (f" · {QP.params(fam, k)[0]} steps" if k != "custom" else "")) for k in (*QP.LEVELS, "custom")], QP.level_of(fam, s.steps, s.cfg), None)
        q_dd.expand = True
        q_dd.height = 44
        steps_tf = tfield("Steps", s.steps, None, 76, number=True, height=44)
        cfg_tf = tfield("CFG", s.cfg, None, 76, number=True, height=44)

        def q_refresh():
            """The level shown follows the numbers; the hint follows the family and the steps."""
            q_dd.value = QP.level_of(fam, S.song.settings.steps, S.song.settings.cfg)
            q_hint.value, warn = QP.hint(fam, S.song.settings.steps)
            q_hint.color = C["amber"] if warn else C["text3"]
            safe_update(q_dd, q_hint)

        def on_quality(e):
            if not S.song or e.control.value == "custom" or e.control.value not in QP.LEVELS:
                return
            S.song.settings.steps, S.song.settings.cfg = QP.params(fam, e.control.value)
            steps_tf.value, cfg_tf.value = str(S.song.settings.steps), str(S.song.settings.cfg)
            mark_dirty(); safe_update(steps_tf, cfg_tf); q_refresh()

        def on_numbers(attr, cast, lo, hi):
            h = sset(attr, cast, lo, hi)
            return lambda e: (h(e), q_refresh())
        q_dd.on_select = synced(on_quality)
        steps_tf.on_change = on_numbers("steps", int, 1, 100)
        cfg_tf.on_change = on_numbers("cfg", float, 0.0, 30.0)
        q_hint.value, _w = QP.hint(fam, s.steps)
        q_hint.color = C["amber"] if _w else C["text3"]
        fmt_now, qual_now = CA.output_format(s.format, s.audio_quality)
        fmt_dd = dd("Output format", [(k, k.upper()) for k in CA.AUDIO_FORMATS], fmt_now, None)
        fmt_dd.expand = True
        fmt_dd.height = 44
        aq_dd = dd("Audio quality", [(q, q) for q in CA.AUDIO_FORMATS[fmt_now]], qual_now or None, None)
        aq_dd.expand = True
        aq_dd.height = 44
        aq_dd.visible = bool(CA.AUDIO_FORMATS[fmt_now])                       # flac has no quality to choose

        def on_format(e):
            if not S.song:
                return
            f, q = CA.output_format(e.control.value, "")
            S.song.settings.format, S.song.settings.audio_quality = f, q
            aq_dd.options = [ft.dropdown.Option(x, x) for x in CA.AUDIO_FORMATS[f]]
            aq_dd.value, aq_dd.visible = (q or None), bool(CA.AUDIO_FORMATS[f])
            mark_dirty(); safe_update(aq_dd)

        def on_audio_quality(e):
            if S.song and e.control.value in CA.AUDIO_FORMATS.get(S.song.settings.format, []):
                S.song.settings.audio_quality = e.control.value
                mark_dirty()
        fmt_dd.on_select, aq_dd.on_select = synced(on_format), synced(on_audio_quality)
        if not names:
            wf_dd.error_text = "Put an ACE-Step 1.5 API workflow in comfy_apis/audio"
        lang_opts = [("auto", "Auto (from the lyrics)")] + [(c, f"{n} ({c})") for c, n in LANGS] + ([] if s.language in ["auto"] + [c for c, _ in LANGS] else [(s.language, s.language)])
        genre = dd("Genre", [(g, g) for g in GENRES] + ([] if s.genre in GENRES else [(s.genre, s.genre)]), s.genre, sset("genre"), editable=True)
        genre.expand = True
        genre.on_text_change = lambda e: (setattr(S.song.settings, "genre", (e.control.value or "").strip()), mark_dirty()) if (e.control.value or "").strip() != S.song.settings.genre else None
        mood = dd("Mood", [(m, m or "None") for m in MOODS] + ([] if s.mood in MOODS else [(s.mood, s.mood)]), s.mood, sset("mood"), editable=True)
        mood.expand = True
        mood.on_text_change = lambda e: (setattr(S.song.settings, "mood", (e.control.value or "").strip()), mark_dirty()) if (e.control.value or "").strip() != S.song.settings.mood else None
        vocal = dd("Vocal Style", VOCALS, s.vocal, sset("vocal"))
        vocal.expand = True
        energy = dd("Energy", ENERGIES, s.energy, sset("energy"))
        energy.expand = True
        lang = dd("Language", lang_opts, s.language, sset("language"), editable=True)
        lang.expand = True
        lang.on_text_change = lambda e: (setattr(S.song.settings, "language", (e.control.value or "").strip()), mark_dirty()) if 0 < len((e.control.value or "").strip()) <= 12 and (e.control.value or "").strip() != S.song.settings.language else None
        key_dd = dd("Key", [(k, k) for k in KEYS] if s.keyscale in KEYS else [(s.keyscale, s.keyscale)] + [(k, k) for k in KEYS], s.keyscale, sset("keyscale"))
        key_dd.expand = True
        ts_opts = list(TIMESIGS) + ([] if str(s.timesignature) in {k for k, _ in TIMESIGS} else [(str(s.timesignature), str(s.timesignature))])      # an unusual stored value stays visible
        ts_dd = dd("Time", ts_opts, str(s.timesignature), sset("timesignature"), None)
        ts_dd.expand = True
        seed_tf = tfield("Seed (-1 = random)", s.seed, sset("seed", int, -1, 2**32 - 1), None, number=True, height=44)
        seed_tf.expand = True
        seed_field_ref["c"] = seed_tf
        for d_ in (genre, mood, vocal, energy, lang, key_dd, ts_dd, wf_dd):
            d_.height = 44
        bpm_tf = tfield("BPM", s.bpm, sset("bpm", int, *BPM_RANGE), None, number=True, height=44)
        bpm_tf.expand = True
        dur_tf = tfield("Duration (sec)", s.duration, sset("duration", int, *DURATION_RANGE), None, number=True, height=44)
        dur_tf.expand = True
        extra_tf = tfield("Extra style tags", s.extra_tags, sset("extra_tags"), None, height=44)
        extra_tf.hint_text = "Add extra style tags (e.g. dreamy, lo-fi, cinematic)"

        def dice(e):
            S.song.settings.seed = CA.fresh_seed(); seed_tf.value = str(S.song.settings.seed); mark_dirty(); safe_update(seed_tf)
        if bb["obj"] is None:                                       # Basic: the multi-select Caption Builder (basic_builder_ui.py). The old Genre / Mood / Vocal / Energy / Extra fields above are no longer shown on it.
            bb["obj"] = BBU.BasicBuilder(ft, dict(C=C, po=po, root=basic_box, settings=lambda: S.song.settings if S.song else None, mark_dirty=mark_dirty, safe_update=safe_update, toast=toast,
                                                  ui_card=ui_card, dd=dd, ibtn=ibtn, field_style=field_style, lang=I18N.lang, pin=I18N.pin_value, show_dialog=lambda d: page.show_dialog(d),
                                                  pop_dialog=lambda: page.pop_dialog(), copy=lambda text: page.run_task(copy_text, text, "Caption"), confirm=confirm))
        bb["obj"].build()
        prompt_tf.value = s.style_prompt
        sg_dd = dd("Who sings", [(k, label) for k, label, _v, _t in AD.SINGERS], AD.singer_in_prompt(s.style_prompt), None)
        sg_dd.height = 44
        sg_dd.tooltip = "Adds the singer to the Music Style Prompt as a phrase you can read and edit (e.g. 'male lead vocal with female backing vocals'). Choosing another one replaces it; 'the AI decides' removes it."

        VOCAL_KEY = {"male vocal": "male", "female vocal": "female", "duet vocals": "duet", "choir": "group", "": "auto"}

        def singer_now() -> str:
            """The singer the song has now: the phrase in the Music Style Prompt if there is one, else what the Vocal Style says."""
            st_ = S.song.settings
            k = AD.singer_in_prompt(st_.style_prompt)
            return k if k != "auto" else VOCAL_KEY.get(st_.vocal or "", "auto")

        def sync_singer_ui():
            """The same choice is shown in the Vocal Style (Basic), Who sings (Advanced) and Who sings (AI Recommend): they always say the same thing."""
            if not S.song:
                return
            k = singer_now()
            changed = []
            for c in (sg_dd, dr_singer):
                if c.value != k:
                    c.value = k
                    changed.append(c)
            if vocal.value != (S.song.settings.vocal or ""):
                vocal.value = S.song.settings.vocal or ""
                changed.append(vocal)
            safe_update(*changed)

        def apply_singer(key: str):
            """One place that applies a singer: the phrase in the Music Style Prompt, the Vocal setting, the caption card, and every control showing it."""
            if not S.song:
                return
            new = AD.set_singer_in_prompt(S.song.settings.style_prompt, key)
            S.song.settings.style_prompt = new
            prompt_tf.value = new
            S.song.settings.vocal = AD.SINGER_BY_KEY.get(key, ("", ""))[1]       # the Vocal setting follows ("the AI decides" = Auto), so Basic, Advanced and AI Recommend agree
            mark_dirty(); safe_update(prompt_tf)
            try:
                render_basic_caption()
            except NameError:
                pass
            sync_singer_ui()

        def on_singer(e):
            apply_singer(e.control.value or "auto")

        def on_vocal(e):
            sset("vocal")(e)                                           # the Vocal Style itself, as before
            v = e.control.value or ""
            if S.song and S.song.settings.vocal == v:
                key = VOCAL_KEY.get(v, "auto")
                S.song.settings.style_prompt = AD.set_singer_in_prompt(S.song.settings.style_prompt, key)
                prompt_tf.value = S.song.settings.style_prompt
                mark_dirty(); safe_update(prompt_tf)
                try:
                    render_basic_caption()
                except NameError:
                    pass
                sync_singer_ui()
        sg_dd.on_select = synced(on_singer)
        singer_hook["fn"] = on_singer
        vocal.on_select = synced(on_vocal)
        sync_singer_ui()
        adv_box.controls = [prompt_tf, sg_dd, saved_btn, history_tile]
        rebuild_saved()
        meta_ctl["f"] = {"bpm": bpm_tf, "key": key_dd, "time": ts_dd, "dur": dur_tf, "lang": lang, "seed": ft.Row([seed_tf, ibtn(ft.Icons.CASINO_OUTLINED, "Random seed", dice, 18, w=32)], spacing=2, expand=True)}
        layout_meta()
        def library_audio():
            """Songs in this library that have at least one take whose audio file is still there."""
            out = []
            for x in S.lib:
                takes = []
                for t in sorted(x["takes"], key=lambda t: t.number):
                    p = ST.take_path(t)
                    if p:
                        takes.append((t.id, f"Take {t.number:02d}", str(p)))
                if takes:
                    out.append((x["song"].id, x["song"].title, takes))
            return out

        cover_file = ft.Text("", size=12, color=C["text2"], expand=True, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)
        cover_song_dd = dd("Source song", [], None, None)
        cover_take_dd = dd("Source take", [], None, None)
        cover_song_dd.expand = cover_take_dd.expand = True
        cover_empty = ft.Text("No takes with audio in this library.", size=12, color=C["text3"], visible=False)
        cover_ext = ft.Column(spacing=6)
        cover_lib = ft.Column([cover_empty, cover_song_dd, cover_take_dd], spacing=6)
        cover_lock = {"on": False}

        def show_cover_file():
            raw = S.song.settings.cover_external if S.song else ""
            if not raw:
                cover_file.value, cover_file.color, cover_file.tooltip = "No file chosen", C["text3"], ""
            else:
                p = Path(raw)
                cover_file.value = p.name if p.is_file() else f"{p.name} (missing)"
                cover_file.color = C["green"] if p.is_file() else C["red"]
                cover_file.tooltip = raw
            safe_update(cover_file)

        def refresh_cover_takes():
            if not S.song:
                return
            choices = library_audio()
            sid = S.song.settings.cover_song_id
            takes = next((ts for i, _t, ts in choices if i == sid), [])
            cover_lock["on"] = True
            cover_take_dd.options = [ft.dropdown.Option(tid, lab) for tid, lab, _p in takes]
            cover_take_dd.value = S.song.settings.cover_take_id if any(tid == S.song.settings.cover_take_id for tid, _l, _p in takes) else (takes[0][0] if takes else None)
            if S.song.settings.cover_take_id != (cover_take_dd.value or ""):
                S.song.settings.cover_take_id = cover_take_dd.value or ""
            cover_lock["on"] = False
            safe_update(cover_take_dd)

        def refresh_cover_lists():
            if not S.song:
                return
            choices = library_audio()
            cover_lock["on"] = True
            cover_song_dd.options = [ft.dropdown.Option(i, title) for i, title, _ts in choices]
            ids = [i for i, _t, _ts in choices]
            if S.song.settings.cover_song_id not in ids:
                S.song.settings.cover_song_id = ids[0] if ids else ""
            cover_song_dd.value = S.song.settings.cover_song_id or None
            cover_empty.visible = not choices
            cover_song_dd.visible = cover_take_dd.visible = bool(choices)
            cover_lock["on"] = False
            refresh_cover_takes()
            show_cover_file()
            safe_update(cover_song_dd, cover_empty)

        def on_cover_song(e):
            if cover_lock["on"] or not S.song:
                return
            S.song.settings.cover_song_id = e.control.value or ""
            S.song.settings.cover_take_id = ""
            mark_dirty()
            refresh_cover_takes()

        def on_cover_take(e):
            if cover_lock["on"] or not S.song:
                return
            S.song.settings.cover_take_id = e.control.value or ""
            mark_dirty()

        cover_song_dd.on_select = synced(on_cover_song)
        cover_take_dd.on_select = synced(on_cover_take)

        async def pick_cover_file(_e=None):
            if not S.song:
                return
            fp = ft.FilePicker()
            files = await fp.pick_files(dialog_title="Cover source audio", allow_multiple=False, allowed_extensions=["wav", "mp3", "flac", "ogg", "m4a", "aac"])
            if not files or not getattr(files[0], "path", None) or not S.song:
                return
            S.song.settings.cover_external = files[0].path
            S.song.settings.cover_source = "external"
            mark_dirty()
            show_cover_file()

        def on_cover_source(e):
            if not S.song:
                return
            S.song.settings.cover_source = e.control.value if e.control.value in ("external", "project") else "external"
            cover_ext.visible = S.song.settings.cover_source != "project"
            cover_lib.visible = S.song.settings.cover_source == "project"
            mark_dirty()
            safe_update(cover_ext, cover_lib)

        def on_cover_toggle(e):
            if not S.song:
                return
            S.song.settings.cover_enabled = bool(e.control.value)
            cover_body.visible = S.song.settings.cover_enabled
            mark_dirty()
            safe_update(cover_body)
            update_generate_button()

        cover_src = ft.RadioGroup(content=ft.Row([ft.Radio(value="external", label="External file"), ft.Radio(value="project", label="This library")], spacing=8, wrap=True),
                                   value=s.cover_source if s.cover_source in ("external", "project") else "external", on_change=on_cover_source)
        cover_ext.controls = [ft.Row([ft.OutlinedButton("Choose file", icon=ft.Icons.FOLDER_OPEN_OUTLINED, height=36, on_click=lambda e: page.run_task(pick_cover_file)), cover_file], spacing=8,
                                      vertical_alignment=ft.CrossAxisAlignment.CENTER)]
        cover_ext.visible = s.cover_source != "project"
        cover_lib.visible = s.cover_source == "project"
        cover_body = ft.Column([ft.Text("The new take is added to this song. The source audio is not changed.", size=11, color=C["text3"]),
                                cover_src, cover_ext, cover_lib], spacing=6, visible=bool(s.cover_enabled))
        cover_check = ft.Checkbox(label="Cover", value=bool(s.cover_enabled), on_change=on_cover_toggle)
        shared_cards = [ui_card(ft.Icons.VIEW_IN_AR_OUTLINED, "Model", [ft.Row([wf_dd, ibtn(ft.Icons.REFRESH_ROUNDED, "Rescan workflows", lambda e: build_settings(), 18, w=34)], spacing=4),
                                                                                                    ft.Row([q_dd, steps_tf, cfg_tf], spacing=8), q_hint,
                                                                                                    ft.Row([fmt_dd, aq_dd], spacing=8)]),
                        ui_card(ft.Icons.TUNE_ROUNDED, "Song Metadata", [meta_body])]
        if FF.ENABLE_COVER:
            refresh_cover_lists()
            cover_ui["refresh"] = refresh_cover_lists
            shared_cards.append(ui_card(ft.Icons.AUDIOTRACK_OUTLINED, "Cover", [cover_check, cover_body]))
        else:
            cover_ui["refresh"] = None
        shared_box.controls = shared_cards
        settings_col.controls = [mode_row, source_note, basic_box, adv_box, ai_box]            # shared_box (Model, Song Metadata) and adv_tile are in the Song Settings tab
        def cell(c, flex: int = 1):
            """One cell of the Advanced grid: the same height for every field and an equal share of the row, so the columns line up."""
            c.expand, c.height, c.width = flex, 44, None
            return c

        def grid_row(*cells):
            return ft.Row(list(cells), spacing=8, vertical_alignment=ft.CrossAxisAlignment.START)
        adv_tile.controls = [grid_row(cell(dd("Seed mode", [("same", "Both seeds equal"), ("fixed", "KSampler own seed")], s.seed_mode, sset("seed_mode"), None), 2),
                                      cell(tfield("KSampler seed", s.ksampler_seed, sset("ksampler_seed", int, 0, 2**32 - 1), None, number=True), 2)),
                             grid_row(cell(tfield("Text CFG", s.text_cfg, sset("text_cfg", float, 0.0, 30.0), None, number=True)),
                                      cell(tfield("Temperature", s.temperature, sset("temperature", float, 0.0, 2.0), None, number=True))),
                             grid_row(cell(tfield("Top P", s.top_p, sset("top_p", float, 0.0, 1.0), None, number=True)),
                                      cell(tfield("Top K", s.top_k, sset("top_k", int, 0, 1000), None, number=True)),
                                      cell(tfield("Min P", s.min_p, sset("min_p", float, 0.0, 1.0), None, number=True))),
                             ft.Text("Output format: MP3 • BPM, key, language and duration are also sent to the model as separate parameters", size=11, color=C["text3"])]
        show_style_tab(style_tab["v"] if style_tab["v"] == "ai" else (s.style_mode if s.style_mode in ("basic", "advanced") else "basic"))
        rebuild_history()
        safe_update(settings_col, adv_tile)

    def style_tags(s: M.SongSettings) -> str:
        return M.style_tags(s)

    # ═══ EDITOR: generation + output ════════════════════════════════════════════════════════════════════════
    btn_generate = ft.FilledButton("Generate", icon=ft.Icons.MUSIC_NOTE_ROUNDED, height=46, expand=True, on_click=lambda e: page.run_task(generate),
                                   style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF", shape=ft.RoundedRectangleBorder(radius=10), text_style=ft.TextStyle(size=15, weight=ft.FontWeight.BOLD)))
    gen_click = {"busy": False}                                        # True from the click until the song is queued / started or the click fails: the button shows a spinner and cannot be pressed twice

    def _gen_spinner(label: str):
        return ft.Row([ft.ProgressRing(width=18, height=18, stroke_width=2.5, color="#FFFFFF"), ft.Text(label, size=15, weight=ft.FontWeight.BOLD, color="#FFFFFF")], spacing=10,
                      alignment=ft.MainAxisAlignment.CENTER, tight=True)

    gen_status = ft.Text("", size=12, color=C["text2"])
    btn_cancel_gen = ft.OutlinedButton("Cancel", height=46, visible=False, on_click=lambda e: request_cancel())
    output_title = ft.Text("Output", size=17, weight=ft.FontWeight.BOLD, color=C["text1"])
    output_count = ft.Text("", size=12, color=C["text3"])
    output_list = ft.ListView(expand=True, spacing=1, padding=po(0, 0, 4, 0))

    def set_gen(status: str, **kw):
        if S.gen is not None:
            S.gen["status"] = status
            S.gen.update(kw)
        rebuild_output()
        update_generate_button()

    btn_repaint = None                                                 # the Repaint tab creates the real button later; this exists so the busy check can see it

    gen_queue: list = []                                               # songs waiting for their turn: prepared requests, run one after the other (FIFO) when the running generation ends

    def update_generate_button():
        busy = S.gen is not None and S.gen.get("status") in ("captioning", "queued", "running")
        busy_any = S.gen is not None and S.gen.get("status") in ("captioning", "queued", "running", "cancelling")
        cover_on = FF.ENABLE_COVER and bool(S.song and S.song.settings.cover_enabled)
        can_queue = busy_any and not cover_on                                   # while one song is generated, Generate adds the open song to the queue (a Cover is started only when nothing runs)
        btn_generate.disabled = (busy_any and not can_queue) or S.song is None
        btn_cancel_gen.visible = busy and S.gen.get("status") != "cancelling"
        safe_update(btn_cancel_gen)
        n_pick = len(dr["multi"]) if not cover_on else 0
        if can_queue:
            btn_generate.content = ("Add to queue" if n_pick < 1 else f"Add {n_pick} {'style' if n_pick == 1 else 'styles'} to queue") + (f" ({len(gen_queue)} waiting)" if gen_queue else "")
        elif n_pick >= 1 and not busy:
            btn_generate.content = f"Generate {n_pick} {'style' if n_pick == 1 else 'styles'}"
        elif busy and S.gen.get("status") == "captioning":
            btn_generate.content = "Creating Caption…"
        elif busy:
            btn_generate.content = "Generating…"
        elif cover_on:
            btn_generate.content = "Cover"
        else:
            btn_generate.content = "Generate"
        if gen_click["busy"]:                                                   # the click is being prepared (the workflow is loaded and checked, the song is copied): a spinner, and no second press
            btn_generate.content = _gen_spinner(btn_generate.content if isinstance(btn_generate.content, str) else "Generate")
            btn_generate.icon = None
            btn_generate.disabled = True
        else:
            btn_generate.icon = ft.Icons.MUSIC_NOTE_ROUNDED
        safe_update(btn_generate)
        if btn_repaint is not None:
            btn_repaint.disabled = busy_any or S.song is None
            safe_update(btn_repaint)

    rate_state = {"task": False}

    async def _rate_later():
        """The library lists (Favorites count, song cards) are redrawn once, a moment after the last star was pressed; the row itself changes at once."""
        try:
            await asyncio.sleep(1.2)
            refresh_lib(); rebuild_song_list(); rebuild_lib_page()
        finally:
            rate_state["task"] = False

    def _star_ctls(song_id: str, t: M.Take, row, size: int) -> list:
        r = int(getattr(t, "rating", 0) or 0)
        return [ft.Container(ft.Icon(ft.Icons.STAR_ROUNDED if i <= r else ft.Icons.STAR_BORDER_ROUNDED, size=size, color=C["amber"] if i <= r else C["text3"]), padding=po(1, 1, 1, 1),
                             ink=True, border_radius=4, tooltip=f"{i} star" if i == 1 else f"{i} stars", on_click=lambda e, i=i: rate_take(song_id, t, i, row, size)) for i in range(1, 6)]

    def rate_take(song_id: str, t: M.Take, n: int, row, size: int):
        """Your own 1-5 star rating of a take; pressing the star that is already the rating takes it away (0 = not rated). A rated take is a take you like (it is in Favorites). Only this row is redrawn."""
        new = 0 if getattr(t, "rating", 0) == n else n
        try:
            ST.update_take(song_id, t.id, rating=new, favorite=new > 0)
        except Exception as ex:
            toast(f"Could not save the rating: {ex}", C["red"]); return
        t.rating, t.favorite = new, new > 0
        row.controls = _star_ctls(song_id, t, row, size)
        safe_update(row)
        if not rate_state["task"]:
            rate_state["task"] = True
            page.run_task(_rate_later)

    def rating_row(song_id: str, t: M.Take, on_changed, size: int = 15):
        row = ft.Row(spacing=0, tight=True)
        row.controls = _star_ctls(song_id, t, row, size)
        return row

    def take_row(song_id: str, title: str, t: M.Take, registry: list, queue_fn, allow_delete=True, on_changed=None, show_title=False, play_fn=None, compact=False):
        pw = 28 if compact else 40
        pbtn = ft.IconButton(ft.Icons.PAUSE_ROUNDED if player.is_playing_take(t.id) else ft.Icons.PLAY_ARROW_ROUNDED, icon_size=17 if compact else 22, width=pw, height=pw, padding=0,
                             icon_color="#FFFFFF", bgcolor=C["accent"], tooltip="Play / pause",
                             on_click=(lambda e: play_fn()) if play_fn else (lambda e: play_take(song_id, title, t, queue_fn)))
        registry.append((t.id, pbtn))
        items = [ft.PopupMenuItem(content=ft.Text("Ask AI about this take…"), on_click=lambda e: open_take_feedback(song_id, t)),
                 ft.PopupMenuItem(content=ft.Text("View Generation Settings"), on_click=lambda e: view_take_settings(title, t)),
                 ft.PopupMenuItem(content=ft.Text("Open as Editable Sheet"), on_click=lambda e: open_take_as_sheet(song_id, t))]
        if FF.ENABLE_REPAINT:
            items.append(ft.PopupMenuItem(content=ft.Text("Repaint"), on_click=lambda e: open_repaint_from(song_id, t)))
        items.append(ft.PopupMenuItem(content=ft.Text("Export Audio"), on_click=lambda e: export_take(title, t)))
        items.append(ft.PopupMenuItem(content=ft.Text("Export to Video…"), on_click=lambda e: open_video_export(title, t)))
        if allow_delete:
            items.append(ft.PopupMenuItem(content=ft.Text("Delete take…"), on_click=lambda e: delete_take_confirm(song_id, t, on_changed)))
        menu = ft.PopupMenuButton(icon=ft.Icons.MORE_VERT_ROUNDED, icon_size=16 if compact else 18, icon_color=C["text2"], items=items)
        playing = player.is_current(t.id)
        dur_txt = fmt_dur(t.duration) if t.duration and t.duration > 0 else "length unknown"
        sub = f"{dur_txt} · {short_date(t.created)[5:]}" if compact else f"{dur_txt}  {short_date(t.created)}"
        name = f"{title} · Take {t.number:02d}" if show_title else f"Take {t.number:02d}"
        wide_enough = (not compact) or side_inner_w() >= 270                   # a narrow sidebar: Export Audio stays in the ⋮ menu only
        repaint_btn = ibtn(ft.Icons.BRUSH_OUTLINED, "Repaint", lambda e: open_repaint_from(song_id, t), 16 if compact else 18, w=26 if compact else 30)
        actions = [pbtn]
        if FF.ENABLE_REPAINT:
            actions.append(repaint_btn)
        if wide_enough:
            actions.append(ibtn(ft.Icons.FILE_DOWNLOAD_OUTLINED, "Export Audio", lambda e: export_take(title, t), 16 if compact else 18, w=26 if compact else 30))
        actions.append(menu)
        if compact:                                                            # one small line per take: more of the list is visible at once
            return ft.Container(padding=po(left=4, right=0, top=1, bottom=1), border_radius=8, bgcolor=C["accent_soft"] if playing else "#00000000",
                                content=ft.Row([cover_box(song_id, 36), ft.Column([ft.Row([ft.Text(name, size=12, weight=ft.FontWeight.W_600, color=C["text1"], no_wrap=True),
                                                                                           ft.Text(sub, size=10, color=C["text3"], no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, expand=True)], spacing=6,
                                                                                          vertical_alignment=ft.CrossAxisAlignment.CENTER),
                                                                                   rating_row(song_id, t, on_changed, 14)], spacing=0, expand=True), *actions], spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        return ft.Container(padding=po(left=8, right=2, top=6, bottom=6), border_radius=10, bgcolor=C["accent_soft"] if playing else "#00000000",
                            content=ft.Row([cover_box(song_id, 44), ft.Column([ft.Text(name, size=14, weight=ft.FontWeight.W_600, color=C["text1"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                                                               ft.Text(sub, size=11, color=C["text3"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                                                               rating_row(song_id, t, on_changed, 15)], spacing=0, expand=True),
                                            *actions], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER))

    OUT_PAGE = 15
    out_limit = {"song": None, "n": OUT_PAGE}

    def rebuild_output():
        reg_output.clear()
        s = S.song
        output_title.value = f"Output — {s.title}" if s else "Output"
        takes = sorted(S.takes, key=lambda t: t.number, reverse=True)
        output_count.value = f"{len(takes)} Take{'s' if len(takes) != 1 else ''}" if s else ""
        rows = []
        g = S.gen
        for msg in (ST.song_problems(s.id) if s else []):
            rows.append(ft.Container(padding=po(10, 8, 10, 8), border_radius=8, bgcolor=C["field"], border=ft.Border.all(1, C["red"]),
                                     content=ft.Text("⚠ " + msg + " Saving to this file is blocked until it is repaired.", size=12, color=C["red"], selectable=True)))
        if s and g and g["song_id"] == s.id and g["status"] in ("captioning", "queued", "running", "cancelling"):
            label = {"captioning": "Creating Music Caption…", "queued": "Queued…", "running": "Generating Audio…", "cancelling": "Cancelling…"}[g["status"]] + (f"  {g['note']}" if g.get("note") else "")
            rows.append(ft.Container(padding=10, border_radius=10, bgcolor=C["panel2"], border=ft.Border.all(1, C["accent"]),
                                     content=ft.Row([ft.ProgressRing(width=20, height=20, stroke_width=3, color=C["accent"]),
                                                     ft.Column([ft.Text(label + (f" · {g['card']}" if g.get("card") else ""), size=13, color=C["text1"]),
                                                                ft.Text(g.get("line", ""), size=11, color=C["text3"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS, visible=bool(g.get("line")))], spacing=1, tight=True, expand=True),
                                                     ft.TextButton("Cancel", on_click=lambda e: request_cancel(), disabled=g["status"] == "cancelling")], spacing=10)))
        if s and g and g["song_id"] != s.id and g["status"] in ("captioning", "queued", "running"):
            rows.append(ft.Text("Another song is being generated. Its result will appear in that song.", size=11, color=C["text3"]))
        for k_, qe in enumerate(gen_queue, 1):                         # the same card as the running one; the newest request is always the last card of the stack
            rows.append(ft.Container(padding=10, border_radius=10, bgcolor=C["panel2"], border=ft.Border.all(1, C["accent"]),
                                     content=ft.Row([ft.Icon(ft.Icons.SCHEDULE_ROUNDED, size=20, color=C["accent"]),
                                                     ft.Column([ft.Text(f"In the queue {k_}: {qe['title']}" + (f" · {qe['sheet']}" if qe.get("sheet") else "") + (f" · {qe['card']}" if qe.get("card") else ""), size=13, color=C["text1"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                                                ft.Text(qe.get("line", ""), size=11, color=C["text3"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS, visible=bool(qe.get("line")))], spacing=1, tight=True, expand=True),
                                                     ft.TextButton("Remove", tooltip="Remove from the queue", on_click=lambda e, i=qe["id"]: queue_remove(i))], spacing=10)))
        if len(gen_queue) > 1:
            rows.append(ft.Row([ft.TextButton("Clear the queue", icon=ft.Icons.PLAYLIST_REMOVE_ROUNDED, on_click=lambda e: queue_clear())], spacing=4))
        if out_limit["song"] != (s.id if s else None):
            out_limit["song"], out_limit["n"] = (s.id if s else None), OUT_PAGE
        for t in takes[:out_limit["n"]]:
            rows.append(take_row(s.id, s.title, t, reg_output, lambda: queue_for_song(s.id, takes[::-1]), on_changed=reload_takes, compact=True))
        if len(takes) > out_limit["n"]:                            # a long list is drawn in parts: opening the tab stays quick
            more_n = len(takes) - out_limit["n"]

            def show_more(e):
                out_limit["n"] += OUT_PAGE
                rebuild_output()
            rows.append(ft.TextButton(f"Show {min(OUT_PAGE, more_n)} more ({more_n} not shown)", on_click=show_more))
        failed = [r for r in S.runs if r.status in ("failed", "cancelled")]
        if failed:
            last = failed[-1]
            rows.append(ft.Container(padding=po(8, 6, 8, 6), border_radius=8, bgcolor=C["field"],
                                     content=ft.Text(f"{len(failed)} attempt(s) without audio • last: {last.status} {short_date(last.created)}" + (f" — {last.error[:90]}" if last.error else ""),
                                                     size=11, color=C["text3"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)))
        if s and not takes and not rows:
            rows.append(ft.Text("No takes yet. Press Generate to make the first one.", size=12, color=C["text3"]))
        output_list.controls = rows
        safe_update(output_title, output_count, output_list)
        if _tl_hook["fn"] is not None:
            _tl_hook["fn"]()

    def reload_takes():
        if S.song:
            S.takes, S.runs = ST.load_takes(S.song.id)
        refresh_lib()
        rebuild_output(); rebuild_song_list(); rebuild_lib_page()
        run_ms_hooks()

    def queue_for_song(song_id: str, takes: list) -> list:
        title = S.song.title if S.song and S.song.id == song_id else (lib_entry(song_id)["song"].title if lib_entry(song_id) else "")
        return [QItem(song_id, t.id, title, f"Take {t.number:02d}", str(ST.take_path(t))) for t in takes if ST.take_path(t)]

    def play_take(song_id: str, title: str, t: M.Take, queue_fn):
        p = ST.take_path(t)
        if not p:
            toast("The audio file of this take is missing", C["red"]); return
        item = QItem(song_id, t.id, title, f"Take {t.number:02d}", str(p))
        try:
            q = queue_fn() if queue_fn else None
        except Exception:
            q = None
        if not player.is_current(t.id):
            S.queue_src = f"{title} · Takes"
        player.play_take(item, q)
        if player.error:
            toast(f"Cannot play: {player.error}", C["red"])

    def view_take_settings(title: str, t: M.Take):
        """Read-only: what this take was generated from. It never changes the open sheet, the editor or the take."""
        d = TS.describe(t)
        rows = []
        for label, val in d["rows"]:
            shown = MISSING_TXT if val is None or val == "" else str(val)
            rows.append(ft.Column([ft.Text(label, size=11, color=C["text3"]),
                                   ft.Text(shown, size=12, color=C["text1"] if shown != MISSING_TXT else C["amber"], selectable=True, font_family="Consolas" if "yrics" in label else None)], spacing=0))
        notes = [ft.Text(n, size=11, color=C["amber"]) for n in d["notes"]]
        page.show_dialog(ft.AlertDialog(title=ft.Text(f"{title} · Take {t.number:02d}"), bgcolor=C["panel"], content=ft.Container(width=520, height=dlg_h(420), content=ft.Column(
            notes + rows, spacing=8, scroll=ft.ScrollMode.AUTO)), actions=[ft.TextButton("Close", on_click=lambda e: page.pop_dialog()),
                                                                           ft.FilledButton("Open as Editable Sheet", on_click=lambda e: (page.pop_dialog(), open_take_as_sheet(t.song_id, t)),
                                                                                           style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))]))

    def open_take_as_sheet(song_id: str, t: M.Take):
        """A new sheet INSIDE this song, made from the take's snapshot (its editor lyrics and options). The take itself is never touched."""
        if t.song_id != song_id:
            toast("This take belongs to another song.", C["red"]); return
        if not (S.song and S.song.id == song_id):
            open_song(song_id)
            if not (S.song and S.song.id == song_id):
                return
        if S.tab != "editor":
            set_tab("editor")
        name, lyrics, settings, warns = TS.editable_copy(t)
        sh = sheet_change(lambda song: SH.from_snapshot(song, name, lyrics, settings, t.id))
        if sh is not None:
            toast(("Opened as a new sheet. " + " ".join(warns))[:320] if warns else f"Opened “{sh.name}” as a new sheet", C["amber"] if warns else C["green"])

    def toggle_fav(song_id: str, t: M.Take, on_changed):
        try:
            ST.update_take(song_id, t.id, favorite=not t.favorite)
        except Exception as ex:
            toast(f"Could not save the favourite: {ex}", C["red"]); return
        (on_changed or reload_takes)()

    def export_take(title: str, t: M.Take):
        try:
            p = ST.export_take(t, title)
            toast(f"Exported: {p}", C["green"])
            try: os.startfile(str(p.parent))
            except Exception: pass
        except Exception as ex:
            toast(f"Export failed: {ex}", C["red"])

    # ═══ EXPORT TO VIDEO: a picture + the take's audio + an animated waveform, made by ffmpeg (music_studio/video_export.py builds and runs the command) ═══════════════════════════════
    def open_video_export(title: str, t: M.Take):
        audio = ST.take_path(t)
        if audio is None:
            toast("The audio file of this take is missing.", C["red"]); return
        song_id, label = t.song_id, f"Take {t.number:02d}"
        vs = VX.load_settings()
        W = 548                                                                       # the width of the waveform (the dialog content is 580 wide with its padding)
        st = {"images": VX.song_images(song_id), "start": 0.0, "end": 0.0, "dur": 0.0, "wave": None, "whole": True, "busy": False, "frac": 0.0, "cancel": None, "drag": None, "anchor": 0.0,
              "ff_ok": None, "ff_msg": "", "png_n": 0, "still_n": 0, "out": ""}
        tmp = Path(tempfile.gettempdir()) / "workflowpad_video"

        def card(title_text: str, icon, *rows):
            return ft.Container(ft.Column([ft.Row([ft.Icon(icon, size=16, color=C["accent"]), ft.Text(title_text, size=12, weight=ft.FontWeight.W_700, color=C["text1"])], spacing=8), *rows], spacing=10),
                                padding=po(14, 12, 14, 14), border_radius=12, bgcolor=C["panel2"], border=ft.Border.all(1, C["border"]))

        # pictures --------------------------------------------------------------------------------------------------------------------------------------------------------------
        pics_row = ft.Row([], spacing=10, run_spacing=10, wrap=True, vertical_alignment=ft.CrossAxisAlignment.START)
        pics_hint = ft.Text("", size=11, color=C["text3"])

        def tile(i: int, path: str):
            icon = i == 0
            img = ft.Container(ft.Image(src=path, width=84, height=84, fit=ft.BoxFit.COVER), width=84, height=84, border_radius=10, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                               border=ft.Border.all(2 if icon else 1, C["accent"] if icon else C["border"]), tooltip="The icon of the song" if icon else None)
            badge = ft.Container(ft.Text(str(i + 1), size=10, weight=ft.FontWeight.BOLD, color="#FFFFFF"), left=4, top=4, bgcolor=C["accent"] if icon else "#000000AA", border_radius=8, padding=po(6, 1, 6, 1))
            tools = ft.Row([ibtn(ft.Icons.CHEVRON_LEFT_ROUNDED, "Move earlier", lambda e, i=i: move_pic(i, -1), 16, w=26, disabled=i == 0),
                            ibtn(ft.Icons.CLOSE_ROUNDED, "Remove this picture", lambda e, i=i: remove_pic(i), 16, w=26),
                            ibtn(ft.Icons.CHEVRON_RIGHT_ROUNDED, "Move later", lambda e, i=i: move_pic(i, 1), 16, w=26, disabled=i >= len(st["images"]) - 1)],
                           spacing=0, alignment=ft.MainAxisAlignment.CENTER, width=84)
            return ft.Column([ft.Stack([img, badge], width=84, height=84), tools], spacing=2, tight=True)

        def show_pictures():
            pics = st["images"]
            pics_row.controls = [tile(i, p) for i, p in enumerate(pics)] + [btn_add]
            pics_hint.value = ("No picture yet. Add one or more: each is shown in turn and moves slowly." if not pics else
                               "Kept for this song. The first picture is also the icon of the song (its centre, as a square). Several pictures change one after the other, in this order.")
            safe_update(pics_row, pics_hint)

        def pictures_changed(new: list):
            st["images"] = new
            show_pictures(); update_state(); icons_changed()

        async def pick_images(_e=None):
            fp = ft.FilePicker()
            files = await fp.pick_files(dialog_title="Pictures for the video", allow_multiple=True, file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=list(VX.IMAGE_TYPES),
                                        initial_directory=vs.image_dir if vs.image_dir and Path(vs.image_dir).is_dir() else None)
            paths = [f.path for f in (files or []) if getattr(f, "path", None)]
            if not paths:
                return
            try:
                new = await asyncio.get_running_loop().run_in_executor(None, VX.add_song_images, song_id, paths)
            except (VX.VideoError, OSError, ValueError) as ex:
                toast(f"The pictures were not added: {ex}", C["red"]); return
            vs.image_dir = str(Path(paths[0]).parent)
            VX.save_settings(vs)
            pictures_changed(new)

        def remove_pic(i: int):
            try:
                pictures_changed(VX.remove_song_image(song_id, i))
            except OSError as ex:
                toast(f"Could not remove the picture: {ex}", C["red"])

        def move_pic(i: int, delta: int):
            try:
                pictures_changed(VX.move_song_image(song_id, i, delta))
            except (VX.VideoError, OSError) as ex:
                toast(f"Could not move the picture: {ex}", C["red"])

        def icons_changed():
            for fn in (rebuild_output, rebuild_song_list, rebuild_lib_page):            # every list that shows the icon: the takes, the songs, the library page (the player follows by itself)
                try:
                    fn()
                except Exception as ex:
                    dbg_once("song icons", ex)

        btn_add = ft.OutlinedButton("Add pictures…", icon=ft.Icons.ADD_PHOTO_ALTERNATE_OUTLINED, on_click=pick_images, height=84)

        # video options ---------------------------------------------------------------------------------------------------------------------------------------------------------
        def keep(**kw):
            for k, v in kw.items():
                setattr(vs, k, v)
            update_state()

        size_dd = dd("Video size", [(k, l) for k, l, _w, _h in VX.SIZES], vs.size, lambda e: (keep(size=e.control.value), size_changed()))
        size_dd.expand = True
        fit_dd = dd("Picture", list(VX.FITS), vs.fit, lambda e: keep(fit=e.control.value))
        fit_dd.expand = True
        w_tf = tfield("Width", vs.width, None, 110, number=True)
        h_tf = tfield("Height", vs.height, None, 110, number=True)
        mode_dd = dd("Wave style", list(VX.WAVE_MODES), vs.wave_mode, lambda e: keep(wave_mode=e.control.value))
        mode_dd.expand = True
        color_dd = dd("Wave colour", list(VX.COLORS), vs.color, lambda e: keep(color=(e.control.value or "").strip()), editable=True)
        color_dd.expand = True
        wh_tf = tfield("Wave height (px)", vs.wave_height or "", None, 150, number=True)
        mg_tf = tfield("Bottom margin (px)", "" if vs.bottom_margin < 0 else vs.bottom_margin, None, 150, number=True)
        defaults_txt = ft.Text("", size=11, color=C["text3"], expand=True)
        motion_dd = dd("Movement of each picture", list(VX.MOTIONS), vs.motion, lambda e: keep(motion=e.control.value))
        motion_dd.expand = True
        trans_dd = dd("Change to the next picture", list(VX.TRANSITIONS), vs.transition, lambda e: keep(transition=e.control.value))
        trans_dd.expand = True
        slide_tf = tfield("Each picture (s)", f"{vs.slide_seconds:g}", None, 140, number=True)
        trs_tf = tfield("Change takes (s)", f"{vs.transition_seconds:g}", None, 140, number=True)
        shuffle_cb = ft.Checkbox(label="Mix the order", value=vs.shuffle, on_change=lambda e: keep(shuffle=bool(e.control.value)))
        custom_row = ft.Row([w_tf, ft.Text("×", color=C["text3"]), h_tf, ft.Text("pixels, both even numbers", size=11, color=C["text3"])], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        def num(tf, default):
            try:
                return int(float((tf.value or "").strip()))
            except ValueError:
                return default

        def fnum(tf, default):
            try:
                return float((tf.value or "").strip().replace(",", "."))
            except ValueError:
                return default

        def edited(_e=None):
            keep(width=num(w_tf, 0), height=num(h_tf, 0), wave_height=num(wh_tf, 0), bottom_margin=num(mg_tf, -1) if (mg_tf.value or "").strip() else -1,
                 slide_seconds=fnum(slide_tf, 0.0), transition_seconds=fnum(trs_tf, 0.0))
        for tf in (w_tf, h_tf, wh_tf, mg_tf, slide_tf, trs_tf):
            tf.on_change = edited

        def size_changed():
            custom_row.visible = vs.size == "custom"
            w, h = vs.dims()
            if w > 0 and h > 0:
                defaults_txt.value = f"Empty = the default of this size ({VX.default_wave_height(w, h)} / {VX.default_margin(w, h)})"
            safe_update(custom_row, defaults_txt)
        size_changed_first = size_changed

        # ffmpeg ----------------------------------------------------------------------------------------------------------------------------------------------------------------
        ff_tf = ft.TextField(label="ffmpeg.exe", value=vs.ffmpeg, hint_text="Choose the file, or leave empty to use the one on the PATH", expand=True, **field_style(text_size=12))
        ff_status = ft.Text("", size=11, color=C["text3"])

        async def test_ff(_e=None):
            vs.ffmpeg = (ff_tf.value or "").strip().strip('"')
            ff_status.value, ff_status.color = "Checking ffmpeg…", C["text3"]
            safe_update(ff_status)
            ok, msg = await asyncio.get_running_loop().run_in_executor(None, VX.test_ffmpeg, vs.ffmpeg)
            st["ff_ok"], st["ff_msg"] = ok, msg
            ff_status.value, ff_status.color = ("✓ " if ok else "✗ ") + msg, C["green"] if ok else C["red"]
            safe_update(ff_status)
            update_state()

        async def pick_ff(_e=None):
            fp = ft.FilePicker()
            files = await fp.pick_files(dialog_title="Choose ffmpeg.exe", allow_multiple=False, file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=["exe"])
            if files and getattr(files[0], "path", None):
                ff_tf.value = files[0].path
                safe_update(ff_tf)
                await test_ff()
        ff_tf.on_change = lambda e: (st.update(ff_ok=None), setattr(ff_status, "value", ""), safe_update(ff_status), update_state())

        # the part of the take ------------------------------------------------------------------------------------------------------------------------------------------------
        wave_img = ft.Image(src="", width=W, height=72, fit=ft.BoxFit.FILL, visible=False)
        wave_msg = ft.Text("Drawing the waveform…", size=11, color=C["text3"])
        sel_box = ft.Container(left=0, top=0, width=0, height=72, bgcolor="#2E2B73FF", border=ft.Border.all(1, "#662B73FF"), visible=False)
        h_a = ft.Container(left=0, top=0, width=4, height=72, bgcolor=C["accent"], border_radius=2, visible=False)
        h_b = ft.Container(left=0, top=0, width=4, height=72, bgcolor=C["accent"], border_radius=2, visible=False)
        stack = ft.Stack([ft.Container(width=W, height=72, bgcolor=C["field"], border_radius=8), wave_img, sel_box, h_a, h_b], width=W, height=72)
        start_tf = tfield("Start", "0:00.00", None, 120)
        end_tf = tfield("End", "0:00.00", None, 120)
        len_txt = ft.Text("", size=12, weight=ft.FontWeight.W_600, color=C["text2"])

        def place():
            d = st["dur"]
            if d <= 0:
                return
            left, wid = WF.selection_rect(W, 0, d, st["start"], st["end"])
            sel_box.left, sel_box.width = left, wid
            h_a.left, h_b.left = max(0, left - 2), min(W - 4, left + wid - 2)
            sel_box.visible = h_a.visible = h_b.visible = not st["whole"]            # the whole take needs no mark: the waveform stays clean until a part is chosen
            safe_update(sel_box, h_a, h_b)

        def write_times(fields: bool = True):
            if fields:
                start_tf.value, end_tf.value = VX.fmt_time(st["start"]), VX.fmt_time(st["end"])
                start_tf.error_text = end_tf.error_text = None
            d, a, b = st["dur"], st["start"], st["end"]
            len_txt.value = (f"Whole take · {VX.fmt_time(d)}" if st["whole"] else f"Part · {VX.fmt_time(a)} → {VX.fmt_time(b)} · {b - a:.1f} s") if d > 0 else ""
            safe_update(start_tf, end_tf, len_txt)
            update_state()

        def whole(_e=None):
            st.update(start=0.0, end=st["dur"], whole=True)
            write_times(); place()

        def _bound():
            d = st["dur"]
            a = min(max(0.0, st["start"]), d)
            b = min(max(a + 0.2, st["end"]), d)
            st["start"], st["end"] = min(a, max(0.0, b - 0.2)), b

        def time_typed(which: str):
            def h(e):
                tf = e.control
                try:
                    v = VX.parse_time(tf.value)
                except ValueError:
                    tf.error_text = "m:ss or seconds"
                    safe_update(tf); return
                if which == "start":
                    st["start"] = v
                else:
                    st["end"] = v
                if st["dur"] > 0 and not (0 <= st["start"] < st["end"] <= st["dur"] + 0.01):
                    tf.error_text = "Inside the take, start before end"
                    safe_update(tf); return
                st["whole"] = False
                tf.error_text = None
                _bound(); write_times(fields=False); place()
                safe_update(tf)
            return h
        start_tf.on_change, end_tf.on_change = time_typed("start"), time_typed("end")

        def _x(e) -> float:
            pos = getattr(e, "local_position", None)
            return float(pos.x if pos is not None else 0)

        def pan_start(e):
            d = st["dur"]
            if d <= 0:
                return
            x, a, b = _x(e), st["start"] / d * W, st["end"] / d * W
            da, db = abs(x - a), abs(x - b)
            if min(da, db) <= 12 and not st["whole"]:
                st["drag"] = "start" if da <= db else "end"
            else:
                st["drag"] = "new"
                st["anchor"] = WF.x_to_time(x, W, 0, d)
                st["start"] = st["end"] = st["anchor"]
            st["whole"] = False

        def pan_update(e):
            d, drag = st["dur"], st["drag"]
            if d <= 0 or drag is None:
                return
            tt = WF.x_to_time(_x(e), W, 0, d)
            if drag == "start":
                st["start"] = min(tt, st["end"] - 0.2)
            elif drag == "end":
                st["end"] = max(tt, st["start"] + 0.2)
            else:
                a, b = sorted((st["anchor"], tt))
                st["start"], st["end"] = a, b
            st["start"] = max(0.0, st["start"])
            st["end"] = min(d, max(st["end"], st["start"]))
            write_times(); place()

        def pan_end(_e=None):
            st["drag"] = None
            if st["dur"] > 0:
                _bound(); write_times(); place()
        gd = ft.GestureDetector(content=stack, drag_interval=16, on_pan_start=pan_start, on_pan_update=pan_update, on_pan_end=pan_end, on_pan_cancel=pan_end,
                                mouse_cursor=ft.MouseCursor.CLICK)

        async def load_wave():
            loop = asyncio.get_running_loop()
            wave, dur = None, 0.0
            try:
                wave = await loop.run_in_executor(None, lambda: WF.load_peaks(str(audio), cache_dir=WF.default_cache_dir()))
                dur = float(wave.duration)
            except Exception as ex:
                dbg_once("video wave", ex)
            if dur <= 0:
                try:
                    ms = await loop.run_in_executor(None, PlayerService.probe_length_ms, str(audio))
                    dur = (ms / 1000.0) if ms else 0.0
                except Exception:
                    dur = 0.0
            st["wave"], st["dur"] = wave, dur
            st["start"], st["end"] = 0.0, dur
            if dur <= 0:
                wave_msg.value, wave_msg.color = "The length of this take could not be read. The whole take will be used.", C["amber"]
                safe_update(wave_msg); update_state(); return
            if wave is not None:
                mins, maxs = WF.visible_peaks(wave, 0, dur, W)
                st["png_n"] += 1
                dest = tmp / f"wave_{st['png_n']}.png"
                try:
                    await loop.run_in_executor(None, lambda: WF.render_png(mins, maxs, dest, W, 72))
                    wave_img.src, wave_img.visible, wave_msg.visible = str(dest), True, False
                except Exception as ex:
                    dbg_once("video wave png", ex)
                    wave_msg.value = "The waveform could not be drawn. You can still type the start and the end."
            else:
                wave_msg.value = "The waveform could not be drawn. You can still type the start and the end."
            safe_update(wave_img, wave_msg)
            write_times(); place()

        def listen(_e=None):
            a, b = st["start"], st["end"]
            if st["dur"] <= 0 or b <= a:
                toast("Choose a part of the take first.", C["amber"]); return
            item = QItem(song_id, t.id, title, label, str(audio))
            if not player.is_current(t.id):
                player.play_take(item, [item])
            err = player.seek_ms(int(a * 1000), song_id, t.id)
            if err:
                toast(err, C["red"]); return
            if player.state != "playing":
                player.toggle()
            _repaint_play["until"] = (song_id, t.id, int(b * 1000))

        btn_whole = ft.TextButton("Whole take", icon=ft.Icons.SELECT_ALL_ROUNDED, on_click=whole)
        btn_listen = ft.OutlinedButton("Listen to the part", icon=ft.Icons.HEADPHONES_ROUNDED, on_click=listen)

        # preview, progress and the buttons ---------------------------------------------------------------------------------------------------------------------------
        still = ft.Image(src="", height=170, fit=ft.BoxFit.CONTAIN, visible=False, border_radius=8)
        still_note = ft.Text("", size=11, color=C["text3"])
        prog = ft.ProgressBar(value=0, visible=False, color=C["accent"], bgcolor=C["field"], height=6, border_radius=3)
        prog_txt = ft.Text("", size=11, color=C["text2"])
        result = ft.Text("", size=11, color=C["green"], selectable=True, visible=False)
        problem_txt = ft.Text("", size=11, color=C["amber"])

        def problem(kind: str) -> str:
            if not st["images"] or not all(Path(p).is_file() for p in st["images"]):
                return "Choose a picture first."
            if st["ff_ok"] is False:
                return "ffmpeg is not ready: " + st["ff_msg"]
            if not VX.find_ffmpeg(vs.ffmpeg):
                return "ffmpeg was not found. Choose ffmpeg.exe above."
            bad = VX.validate(vs)
            if bad:
                return bad[0]
            if not st["whole"] and st["end"] - st["start"] < 0.5:
                return "The part is too short."
            return ""

        def update_state():
            why = problem("video")
            problem_txt.value = "" if st["busy"] else why
            for b in (btn_still, btn_clip, btn_export):
                b.disabled = st["busy"] or bool(why)
            btn_stop.visible = st["busy"]
            btn_close.disabled = False
            safe_update(problem_txt, btn_still, btn_clip, btn_export, btn_stop)

        def job(kind: str):
            vs.ffmpeg = (ff_tf.value or "").strip().strip('"')
            VX.save_settings(vs)
            tmp.mkdir(parents=True, exist_ok=True)
            a, ln = (0.0, 0.0) if st["whole"] else (st["start"], st["end"] - st["start"])
            if kind == "video":
                out = VX.unique_output(ST.exports_dir(), title, t.number, ".mp4", "" if st["whole"] else " (part)")
            elif kind == "preview":
                out = tmp / "preview.mp4"
            else:
                st["still_n"] += 1
                out = tmp / f"still_{st['still_n']}.png"
            return VX.Job(list(st["images"]), str(audio), str(out), vs, a, ln, kind)

        async def ticker():
            while st["busy"]:
                if st.get("indeterminate"):
                    prog.value = None
                else:
                    prog.value = st["frac"]
                    prog_txt.value = f"{int(st['frac'] * 100)} %"
                safe_update(prog, prog_txt)
                await asyncio.sleep(0.25)

        async def go(kind: str, _e=None):
            if st["busy"] or problem(kind):
                return
            j = job(kind)
            ev = threading.Event()
            st.update(busy=True, frac=0.0, cancel=ev, indeterminate=(kind == "still"))
            prog.visible, prog.value = True, (None if kind == "still" else 0)
            prog_txt.value = {"video": "Making the video…", "preview": "Making a short preview…", "still": "Drawing one frame…"}[kind]
            result.visible = False
            safe_update(prog, prog_txt, result)
            update_state()
            page.run_task(ticker)
            err, out = "", None
            try:
                out = await asyncio.get_running_loop().run_in_executor(None, lambda: VX.run(j, lambda f: st.__setitem__("frac", f), ev))
            except VX.VideoCancelled:
                err = "Stopped. No file was made."
            except VX.VideoError as ex:
                err = str(ex)
            except Exception as ex:
                err = f"Unexpected error: {ex}"
            st.update(busy=False, cancel=None, indeterminate=False)
            prog.visible = False
            safe_update(prog)
            if err:
                prog_txt.value, prog_txt.color = err, C["red"] if not err.startswith("Stopped") else C["text2"]
                safe_update(prog_txt); update_state(); return
            prog_txt.value, prog_txt.color = "", C["text2"]
            if kind == "still":
                still.src, still.visible = str(out), True
                still_note.value = "The picture of the video at the size you chose (reduced here). The real waveform moves with the music."
                safe_update(still, still_note, prog_txt)
            elif kind == "preview":
                safe_update(prog_txt)
                try:
                    os.startfile(str(out))
                except OSError as ex:
                    toast(f"The preview was made but could not be opened: {ex}", C["amber"])
            else:
                st["out"] = str(out)
                result.value, result.visible = f"Saved: {out}", True
                btn_show.visible = True
                safe_update(result, btn_show, prog_txt)
                toast("The video was saved.", C["green"])
            update_state()

        def stop(_e=None):
            ev = st.get("cancel")
            if ev is not None:
                ev.set()

        def show_folder(_e=None):
            try:
                if st["out"]:
                    subprocess.Popen(["explorer", "/select,", st["out"]])
            except OSError as ex:
                toast(f"Could not open the folder: {ex}", C["amber"])

        def close(_e=None):
            vs.ffmpeg = (ff_tf.value or "").strip().strip('"')
            VX.save_settings(vs)
            stop()
            page.pop_dialog()

        btn_still = ft.OutlinedButton("Preview image", icon=ft.Icons.IMAGE_SEARCH_ROUNDED, on_click=lambda e: page.run_task(go, "still"))
        btn_clip = ft.OutlinedButton("Preview clip", icon=ft.Icons.PLAY_CIRCLE_OUTLINE_ROUNDED, on_click=lambda e: page.run_task(go, "preview"),
                                     tooltip="Makes about 8 seconds, small and fast, and opens it in your video player")
        btn_export = ft.FilledButton("Export video", icon=ft.Icons.MOVIE_CREATION_OUTLINED, on_click=lambda e: page.run_task(go, "video"), style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))
        btn_stop = ft.OutlinedButton("Stop", icon=ft.Icons.STOP_ROUNDED, on_click=stop, visible=False)
        btn_close = ft.TextButton("Close", on_click=close)
        btn_show = ft.TextButton("Show in folder", icon=ft.Icons.FOLDER_OPEN_ROUNDED, on_click=show_folder, visible=False)
        btn_ff = ft.OutlinedButton("Choose…", icon=ft.Icons.FOLDER_OPEN_ROUNDED, on_click=pick_ff)
        btn_ff_test = ft.TextButton("Test", on_click=test_ff)

        body = ft.Column([
            card("Pictures", ft.Icons.IMAGE_OUTLINED, pics_row, pics_hint),
            card("Video", ft.Icons.TUNE_ROUNDED,
                 ft.Row([size_dd, fit_dd], spacing=10), custom_row,
                 ft.Row([mode_dd, color_dd], spacing=10),
                 ft.Row([wh_tf, mg_tf, defaults_txt], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER)),
            card("Movement", ft.Icons.SLIDESHOW_ROUNDED,
                 ft.Row([motion_dd, trans_dd], spacing=10),
                 ft.Row([slide_tf, trs_tf, shuffle_cb], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                 ft.Text("Every picture moves slowly while it is shown, then changes to the next. With one picture, the zoom goes in and out. The pictures repeat until the music ends.", size=11, color=C["text3"])),
            card("Part of the take", ft.Icons.CONTENT_CUT_ROUNDED,
                 ft.Text("Drag on the waveform to choose a part, or drag its edges. Nothing chosen = the whole take.", size=11, color=C["text3"]),
                 ft.Stack([gd, ft.Container(wave_msg, padding=po(12, 26, 12, 0), ignore_interactions=True)], width=W, height=72),
                 ft.Row([start_tf, end_tf, ft.Container(expand=True), btn_whole, btn_listen], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                 len_txt),
            card("ffmpeg", ft.Icons.TERMINAL_ROUNDED, ft.Row([ff_tf, btn_ff, btn_ff_test], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER), ff_status),
            ft.Column([still, still_note], spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Column([prog, ft.Row([prog_txt, ft.Container(expand=True), btn_stop], vertical_alignment=ft.CrossAxisAlignment.CENTER), ft.Row([ft.Container(result, expand=True), btn_show], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER), problem_txt], spacing=6),
        ], spacing=12, scroll=ft.ScrollMode.AUTO, width=W + 32)

        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], inset_padding=po(16, 16, 16, 16),
                                        title=ft.Row([ft.Icon(ft.Icons.MOVIE_CREATION_OUTLINED, size=22, color=C["accent"]),
                                                      ft.Column([ft.Text("Export to Video", size=18, weight=ft.FontWeight.BOLD), ft.Text(f"{title} · {label}", size=11, color=C["text3"])], spacing=0, tight=True)], spacing=10),
                                        content=ft.Container(body, width=W + 32, height=dlg_h(640, 0.72)),
                                        actions=[btn_close, btn_still, btn_clip, btn_export], actions_padding=po(16, 4, 16, 14), actions_alignment=ft.MainAxisAlignment.END))
        show_pictures(); size_changed_first(); update_state()
        page.run_task(load_wave)
        if VX.find_ffmpeg(vs.ffmpeg):
            page.run_task(test_ff)
        else:
            ff_status.value, ff_status.color = "ffmpeg was not found. Choose ffmpeg.exe (download it from ffmpeg.org if you do not have it).", C["amber"]
            safe_update(ff_status)


    def purge_queue(song_id: str | None = None, take_id: str | None = None):
        """Take the tracks of a deleted song / take out of the playing queue. The track that is playing is released first (its file is about to be deleted); anything else keeps playing."""
        gone = lambda q: bool((song_id and q.song_id == song_id) or (take_id and q.take_id == take_id))
        cur = player.current
        if cur and gone(cur):
            player.release()
            cur = None
        keep = [q for q in player.queue if not gone(q)]
        if len(keep) != len(player.queue):
            player.queue = keep
            player.index = keep.index(cur) if cur in keep else -1
            player._notify()

    def delete_take_confirm(song_id: str, t: M.Take, on_changed):
        def do():
            purge_queue(take_id=t.id)                     # releases a playing file first (Windows cannot delete an open file)
            try:
                ST.delete_take(song_id, t.id)
            except Exception as ex:
                toast(f"Take not deleted: {ex}", C["red"])
                return
            (on_changed or reload_takes)()
        confirm("Delete take", f"Delete Take {t.number:02d}? The audio file is removed.", "Delete", do, danger=True)

    def request_cancel():
        if S.gen and S.gen["status"] in ("captioning", "queued", "running"):
            S.gen["cancel"] = True
            set_gen("cancelling")

    async def check_connection_loop():
        loop = asyncio.get_running_loop()
        while True:
            ok = await loop.run_in_executor(None, CA.is_connected, S.host)
            S.conn = ok
            conn_dot.bgcolor = C["green"] if ok else C["red"]
            conn_txt.value = "ComfyUI connected" if ok else "ComfyUI not connected"
            safe_update(conn_dot, conn_txt)
            g = S.gen
            if g and g["status"] in ("captioning", "queued", "running", "cancelling"):          # the generation thread only changes the status; show it (Queued -> Generating, "ComfyUI is not responding")
                shown = (g["status"], g.get("note", ""))
                if g.get("shown") != shown:
                    g["shown"] = shown
                    rebuild_output()
            await asyncio.sleep(3)

    # Basic mode has no Effective Caption card any more: its Caption Preview (basic_builder_ui.py) IS the caption Generate sends. This card now only shows the Advanced prompt.
    cap_open = {"v": False}
    cap_text = ft.Text("", size=12, color=C["text1"], selectable=True)
    cap_src = ft.Text("", size=10, color=C["text3"], expand=True)
    cap_chev = ft.Icon(ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED, size=20, color=C["text2"])
    cap_copy = ibtn(ft.Icons.CONTENT_COPY_ROUNDED, "Copy the caption", lambda e: page.run_task(copy_text, cap_text.value, "Caption"), 16, w=28)
    cap_body = ft.Container(ft.Column([ft.Container(cap_text, padding=8, border_radius=8, bgcolor=C["field"], border=ft.Border.all(1, C["border"])),
                                       ft.Row([cap_src, cap_copy], vertical_alignment=ft.CrossAxisAlignment.CENTER)], spacing=4, tight=True, scroll=ft.ScrollMode.AUTO),
                            visible=False, padding=po(0, 6, 0, 0), height=170)

    def toggle_cap(e=None):
        cap_open["v"] = not cap_open["v"]
        cap_body.visible = cap_open["v"]
        cap_chev.icon = ft.Icons.KEYBOARD_ARROW_UP_ROUNDED if cap_open["v"] else ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED
        safe_update(cap_body, cap_chev)
    cap_card = ft.Container(ft.Column([ft.Container(ft.Row([ft.Icon(ft.Icons.DESCRIPTION_OUTLINED, size=15, color=C["text2"]),
                                                            ft.Column([ft.Text("Effective Caption", size=12, weight=ft.FontWeight.W_600, color=C["text1"]), ft.Text("Sent to ACE-Step", size=10, color=C["text3"])], spacing=0, expand=True),
                                                            cap_chev], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER), ink=True, on_click=toggle_cap, border_radius=6),
                                       cap_body], spacing=0, tight=True), padding=po(10, 8, 10, 8), border_radius=10, bgcolor=C["panel2"], border=ft.Border.all(1, C["border"]), visible=False)

    def render_basic_caption():
        """Advanced: the active Music Style Prompt as it is sent. Basic: no card (the Basic page has the one Caption Preview)."""
        song = S.song
        if not song or style_tab["v"] == "ai" or song.settings.style_mode != "advanced":
            cap_card.visible = False
            safe_update(cap_card)
            return
        text, src = (song.settings.style_prompt or "").strip(), "Your Music Style Prompt"
        cap_text.value = text or "Write a Music Style Prompt (Advanced tab) to see what is sent."
        cap_text.color = C["text1"] if text else C["text3"]
        cap_src.value = src
        cap_src.color = C["text3"]
        cap_copy.visible = bool(text)
        cap_card.visible = True
        safe_update(cap_card, cap_text, cap_src, cap_copy)

    async def ask_long_caption(n: int) -> bool:
        """The caption is longer than the 512 characters suggested for ACE-Step. It is never cut: the user chooses to send it as it is, or to go back and shorten it."""
        fut = asyncio.get_running_loop().create_future()

        def done(v: bool):
            page.pop_dialog()
            if not fut.done():
                fut.set_result(v)
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Text("The caption is long"),
                                        content=ft.Container(ft.Text(f"The caption has {n} characters; {CB.CAPTION_GUIDE_MAX} are suggested for ACE-Step. It will be sent exactly as it is (nothing is cut). Shorter captions are usually followed better.", size=12, color=C["text1"]), width=420),
                                        actions=[ft.TextButton("Back", on_click=lambda e: done(False)),
                                                 ft.FilledButton("Generate anyway", on_click=lambda e: done(True), style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))]))
        return await fut

    def resolve_take_file(song_id: str, take_id: str):
        """A take audio file in this library, or None. Used by Cover; it never writes the file."""
        entry = lib_entry(song_id)
        takes = entry["takes"] if entry else []
        if not any(x.id == take_id for x in takes):
            try:
                takes, _runs = ST.load_takes(song_id)
            except Exception:
                return None
        found = next((x for x in takes if x.id == take_id), None)
        return ST.take_path(found) if found else None

    def release_gen(gen):
        """Give the busy state back for THIS generation only. S.gen belongs to the job that set it last: a job that ends late never clears the state of a newer one."""
        if gen is not None and S.gen is gen:
            S.gen = None

    def finish_generation(song_id: str):
        """After a generation ended in an unexpected way (an exception, a cancelled task): the buttons are idle again and the takes are read again, so a take that WAS made is still listed."""
        try:
            refresh_lib()
            if S.song and S.song.id == song_id:
                S.takes, S.runs = ST.load_takes(song_id)
        except Exception as ex:
            log.warning("generation cleanup could not read the takes again: %r", ex)
        try:
            update_generate_button(); rebuild_output(); rebuild_song_list(); rebuild_lib_page()
        except Exception as ex:
            log.warning("generation cleanup could not redraw: %r", ex)

    async def guarded_generation(body, label: str):
        """Run one generation job (`body(own)`): whatever ends it, its busy state is released (only if it is still its own) and the user is told what happened. Normal results are reported by the job itself."""
        own = {"gen": None, "song_id": S.song.id if S.song else ""}
        try:
            await body(own)
        except asyncio.CancelledError:
            release_gen(own["gen"])
            finish_generation(own["song_id"])
            raise
        except Exception as ex:
            log.exception("%s: unexpected error", label)
            release_gen(own["gen"])
            finish_generation(own["song_id"])
            toast(f"{label} failed unexpectedly: {ex}. See the log.", C["red"])
        finally:
            release_gen(own["gen"])

    def _gen_click_done():
        if gen_click["busy"]:
            gen_click["busy"] = False
            update_generate_button()

    async def generate():
        if gen_click["busy"]:
            return
        gen_click["busy"] = True
        update_generate_button()
        await asyncio.sleep(0.05)                                # let the spinner reach the screen before the (synchronous) preparation starts
        try:
            await _generate_click()
        finally:
            _gen_click_done()

    async def _generate_click():
        cards = dr_multi_cards() if style_tab["v"] == "ai" else []
        if cards is None:
            return
        if cards and S.song and FF.ENABLE_COVER and S.song.settings.cover_enabled:
            toast("A Cover is made from the song as it is. Clear the picked styles or turn Cover off.", C["amber"]); return
        if cards:
            await guarded_generation(lambda own: _generate_many(own, cards), "Generate")
            return
        await guarded_generation(_generate_body, "Generate")

    async def _generate_many(own, cards: list):
        """Several picked styles: one song per card, each prepared from the song with THAT card's settings (the editor is not changed). All of them are queued in the order of the cards; the first
        starts at once when nothing is running."""
        entries: list = []
        for card in cards:
            n0 = len(entries)
            await _generate_body(own, card, entries)
            if len(entries) == n0:
                return                                           # a check failed and said why: nothing is queued
        busy_now = S.gen is not None and S.gen["status"] in ("captioning", "queued", "running", "cancelling")
        _gen_click_done()
        dr["multi"] = []
        if style_tab["v"] == "ai":
            dr_render()
        if busy_now:
            gen_queue.extend(entries)
            update_generate_button(); rebuild_output()
            toast(f"{len(entries)} style(s) added to the queue.", C["green"])
            return
        gen_queue.extend(entries[1:])
        update_generate_button(); rebuild_output()
        toast(f"{len(entries)} style(s): the first starts now, the others wait in the queue.", C["green"])
        await _run_prepared(own, entries[0])
        await _drain_queue(own)

    def _queue_line(st: M.SongSettings) -> str:
        """What a queued song will use, for the Output panel: the values really sent to ACE-Step."""
        bits = [f"BPM {st.bpm}", f"Key {st.keyscale}", f"Time {dict(TIMESIGS).get(str(st.timesignature), st.timesignature)}"]
        for lbl, n in (("Mood", "mood"), ("Vocal", "vocal"), ("Energy", "energy")):
            v = getattr(st, n, "")
            if v:
                bits.append(f"{lbl} {dr_show(n, v)}")
        return " · ".join(bits)

    async def _generate_body(own, card=None, collect=None):
        if not S.song:
            return
        if S.gen and S.gen["status"] in ("captioning", "queued", "running", "cancelling") and FF.ENABLE_COVER and S.song.settings.cover_enabled:
            toast("A song is already being generated. A Cover starts when nothing else is running."); return
        song = S.song
        s = song.settings
        if card:                                                  # one picked style: the song's settings with THIS card's values on top (BPM, key, time signature, mood, vocal, energy, caption...)
            s = SH.settings_from_dict({**SH.settings_to_dict(song.settings), **card["changes"]})
            CB.follow_legacy(s, card["changes"])
        comp = K.compile_lyrics(song.lyrics)
        tags = style_tags(s)
        instrumental = comp.ace.strip().lower() == "[instrumental]"                  # an instrumental song: the lyrics field is just the [Instrumental] tag (as the tutorial describes)
        if not comp.ace.strip() or (not instrumental and not any(k in ("text", "hold") for k, _ in comp.lines)):
            toast("Write some lyrics first (or only [Instrumental] for a song without singing)", C["amber"]); return
        if not tags:
            toast("Write the Music Style Prompt first" if s.style_mode == "advanced" else "Choose a Genre first", C["amber"]); return
        if s.style_mode != "advanced":                            # Basic: `tags` IS the Caption Preview (caption_builder.compose of the chips): no AI writes or rewrites it, and it is never cut
            n_chars, level = CB.length_status(tags)
            if level == "too_long":
                toast(f"The caption has {n_chars} characters; the limit is {CB.CAPTION_HARD_MAX}. Remove some chips or notes (nothing is cut for you).", C["red"]); return
            if level == "long" and not await ask_long_caption(n_chars):
                return
        if FF.ENABLE_COVER and s.cover_enabled:
            try:
                KJ.cover_audio_path(s, resolve_take_file)         # fail before the caption step; the file is checked again on the snapshot
            except KJ.JobError as ex:
                toast(str(ex), C["red"]); return
        elif not s.workflow:
            toast("No ACE-Step workflow in comfy_apis/audio. Export one in API format and put it there.", C["red"]); return
        rp_show_tab("results")                                    # Generate was accepted: the Results tab shows the progress (and the Cancel button) and then the new take
        SH.flush(song)                                            # the active sheet is brought up to date before anything is captured
        snap = M.song_from_dict(M.song_to_dict(song))             # the snapshot of THIS song: nothing after this point reads the editor
        if card:
            snap.settings = SH.settings_from_dict(SH.settings_to_dict(s))
        sheet = SH.active_sheet(song)
        before = SH.settings_to_dict(s)               # the options exactly as the user had them (seed -1, language "auto"), before they are resolved
        song_id, title = snap.id, snap.title
        try:                                                      # Preflight (never blocks, no AI): vocal cues that ask for opposite things at the same time. Generation goes on; the user decides whether to edit.
            risky = VC.preflight(snap.lyrics, tags)
            if risky:
                toast("Vocal cues may conflict, so ACE-Step may follow only one: " + "; ".join(risky[:2]) + (f" (+{len(risky) - 2} more)" if len(risky) > 2 else "") + ". Generating anyway; see the Vocal Analysis report.", C["amber"])
        except Exception as ex:
            log.debug("vocal cue preflight failed: %r", ex)
        loop = asyncio.get_running_loop()
        cover_on = FF.ENABLE_COVER and bool(snap.settings.cover_enabled)
        gen = None
        if cover_on:
            try:
                src_path = KJ.cover_audio_path(snap.settings, resolve_take_file)
            except KJ.JobError as ex:
                toast(str(ex), C["red"]); return
            S.gen = own["gen"] = {"song_id": song_id, "status": "queued", "pid": "", "cancel": False, "note": "Uploading source audio…"}
            update_generate_button(); rebuild_output()
            gen = S.gen
            try:
                wf, used = await loop.run_in_executor(None, lambda: KJ.prepare_cover(
                    AUDIO_API_DIR, snap.settings, host=S.host, audio_path=src_path, caption=tags, lyrics=comp.ace, instrumental=instrumental))
            except Exception as ex:
                S.gen = None
                update_generate_button(); rebuild_output()
                toast(f"Cannot prepare the cover: {ex}", C["red"]); return
            if gen.get("cancel"):
                S.gen = None
                update_generate_button(); rebuild_output()
                toast("Generation cancelled", C["amber"]); return
        else:
            try:
                wf, used = G.prepare(AUDIO_API_DIR, snap, tags, comp.ace)
            except Exception as ex:
                toast(f"Cannot prepare the workflow: {ex}", C["red"]); return
        resolved_lang = used.get("language") if cover_on else snap.settings.language
        gen_snapshot = TS.snapshot_extra(sheet.id, sheet.name, song.lyrics, before, resolved_lang)
        if not card and s.style_mode == "advanced" and S.song is not None and S.song.id == song_id and M.add_prompt_history(s, tags):      # the prompt that is really sent is remembered (no duplicates)
            mark_dirty(); rebuild_history()
        if S.song is not None and S.song.id == song_id:
            save_song_now()
        if not cover_on:
            entry = {"id": M.new_id("q_"), "song_id": song_id, "title": title, "sheet": sheet.name, "snap": snap, "wf": wf, "used": used, "tags": tags, "ace": comp.ace, "gen_snapshot": gen_snapshot,
                     "card": card["name"] if card else "", "line": _queue_line(snap.settings)}
            if collect is not None:
                collect.append(entry)                                # several picked styles: the caller queues them all together
                return
            if S.gen and S.gen["status"] in ("captioning", "queued", "running", "cancelling"):
                gen_queue.append(entry)                              # another song is being generated: this one is prepared now (what the editor holds NOW) and waits its turn
                _gen_click_done()
                update_generate_button(); rebuild_output()
                toast(f"“{title}” was added to the queue (place {len(gen_queue)}).", C["green"])
                return
            await _run_prepared(own, entry)
            await _drain_queue(own)
            return
        gen["note"] = ""

        def on_state(st):
            if st == "unreachable":
                gen["note"] = "ComfyUI is not responding…"              # shown in the Output panel; the wait gives up on its own after ~30 s
                return
            gen["note"] = ""
            if st == "running" and gen["status"] != "cancelling":
                gen["status"] = "running"
        if cover_on:
            res = await loop.run_in_executor(None, lambda: KJ.execute(
                S.host, snap, wf, used, cancel_fn=lambda: gen["cancel"], on_state=on_state,
                on_submitted=lambda pid: gen.update(pid=pid), snapshot=gen_snapshot, lyrics=comp.ace, tags=tags))
        else:
            res = await loop.run_in_executor(None, lambda: G.execute(S.host, snap, wf, used, tags, comp.ace, lambda: gen["cancel"], on_state, lambda pid: gen.update(pid=pid), gen_snapshot))
        release_gen(gen)
        _after_generation(res, song_id, title)
        await _drain_queue(own)

    def _after_generation(res, song_id: str, title: str):
        refresh_lib()
        if S.song and S.song.id == song_id:
            S.takes, S.runs = ST.load_takes(song_id)
        update_generate_button(); rebuild_output(); rebuild_song_list(); rebuild_lib_page()
        run_ms_hooks()
        if res.status == "completed":
            toast(f"“{title}”: Take {res.take.number:02d} is ready", C["green"])
        elif res.status == "cancelled":
            toast("Generation cancelled", C["amber"])
        else:
            toast(f"Generation failed: {res.error}", C["red"])

    async def _run_prepared(own, e: dict):
        """Run one prepared (queued or direct) generation: everything it needs was captured when it was requested; nothing is read from the editor now."""
        loop = asyncio.get_running_loop()
        gen = S.gen = own["gen"] = {"song_id": e["song_id"], "status": "queued", "pid": "", "cancel": False, "card": e.get("card", ""), "line": e.get("line", "")}
        gen_click["busy"] = False                                # the click is done: from here the button says Generating… / the Output panel shows the progress
        update_generate_button(); rebuild_output()

        def on_state(st):
            if st == "unreachable":
                gen["note"] = "ComfyUI is not responding…"
                return
            gen["note"] = ""
            if st == "running" and gen["status"] != "cancelling":
                gen["status"] = "running"
        res = await loop.run_in_executor(None, lambda: G.execute(S.host, e["snap"], e["wf"], e["used"], e["tags"], e["ace"], lambda: gen["cancel"], on_state, lambda pid: gen.update(pid=pid), e["gen_snapshot"]))
        release_gen(gen)
        _after_generation(res, e["song_id"], e["title"])

    async def _drain_queue(own):
        """The next waiting song starts as soon as the one before it has ended (finished, failed or cancelled): in the same task, so nothing can slip in between."""
        while gen_queue:
            nxt = gen_queue.pop(0)
            update_generate_button(); rebuild_output()
            toast(f"Next in the queue: “{nxt['title']}”", C["accent"])
            try:
                await _run_prepared(own, nxt)
            except Exception as ex:
                log.exception("queued generation failed")
                release_gen(own.get("gen"))
                finish_generation(nxt["song_id"])
                toast(f"“{nxt['title']}” failed unexpectedly: {ex}. See the log.", C["red"])

    def queue_remove(qid: str):
        gen_queue[:] = [x for x in gen_queue if x["id"] != qid]
        update_generate_button(); rebuild_output()

    def queue_clear():
        gen_queue.clear()
        update_generate_button(); rebuild_output()
        toast("The queue was cleared. The song that is running continues.", C["amber"])

    # ═══ EDITOR: AI chat ════════════════════════════════════════════════════════════════════════════════════
    # The chat list is a scrolling Column that KEEPS its children between updates: a new answer appends one control and an Apply / Ignore replaces only its own bubble, so the scroll position is never
    # reset by a rebuild. The framework's auto_scroll is off: the program scrolls to the end itself, and only when the user sent a message or was already reading the end.
    chat_scroll = {"pixels": 0.0, "max": 0.0, "at_end": True, "keys": [], "ctx": None, "saved": {}, "wait": False}
    chat_list = ft.Column(expand=True, spacing=8, scroll=ft.ScrollMode.AUTO, auto_scroll=False, on_scroll=lambda e: _chat_scrolled(e))
    chat_new_chip = ft.TextButton("New message ↓", icon=ft.Icons.ARROW_DOWNWARD_ROUNDED, visible=False, on_click=lambda e: page.run_task(_chat_to_end), style=ft.ButtonStyle(color="#FFFFFF", bgcolor=C["accent"], padding=po(14, 4, 14, 4), shape=ft.RoundedRectangleBorder(radius=16)))       # a filled pill (a plain text link was not noticed)
    _chat_focus = {"on": False}                                       # does the message box hold the keyboard focus (so it can be given back after the chat list is redrawn)

    async def refocus_chat(had_focus: bool):
        """After an answer the chat list is redrawn. When the message box held the focus at that moment it is asked for the focus again (even if the redraw made it let go), so the arrow keys,
        Backspace and Delete keep working in it."""
        if not had_focus:
            return
        await asyncio.sleep(0.3)
        try:
            await chat_tf.focus()
        except Exception as ex:
            dbg_once("chat focus", ex)
    chat_tf = ft.TextField(hint_text="Ask the AI to write or change lyrics...", multiline=True, min_lines=1, max_lines=3, shift_enter=True, on_submit=lambda e: page.run_task(send_chat),
                           on_focus=lambda e: _chat_focus.update(on=True), on_blur=lambda e: _chat_focus.update(on=False, blur_t=time.time()),
                           **field_style(expand=True, height=None, hint_style=ft.TextStyle(color=C["text3"])))
    chat_busy = ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False)
    backend_dd = ft.Dropdown(width=190, **field_style(height=34, text_size=11))
    an_backend_dd = ft.Dropdown(expand=True, **field_style(height=34, text_size=11))        # the same choice, shown in the Analysis tab

    def backend_options():
        opts = [(f"local:{n}", f"Local LLM • {n[:-5]}") for n in LC.list_local_workflows(LLM_DIR)]
        if LC.grok_creds()["key"]:
            opts.append(("grok", "Grok (xAI)"))
        return opts

    def init_backend_dd():
        opts = backend_options()
        keys = [k for k, _ in opts]
        if S.backend not in keys:
            S.backend = keys[0] if keys else ""
        for dd_ in (backend_dd, an_backend_dd):
            dd_.options = [ft.dropdown.Option(k, t) for k, t in opts]
            dd_.value = S.backend or None
            dd_.hint_text = "No AI backend found" if not keys else None
            dd_.on_select = synced(on_backend_pick)

    def on_backend_pick(e):
        S.backend = e.control.value
        backend_dd.value = an_backend_dd.value = S.backend
        cfg.update({"chat_backend": S.backend}); _save_cfg(cfg)
        safe_update(backend_dd, an_backend_dd)
        render_analysis()
    init_backend_dd()

    def detach_chat_selection(e=None):
        """The next question does not include this slice. The lyrics, the caret, the editor selection and the message box stay."""
        if S.song:
            snap = _chat_snap["snap"]
            if isinstance(snap, dict) and snap.get("song_id") == S.song.id and snap.get("sheet_id") == S.song.book.active_id:
                _chat_detach["key"] = (snap["song_id"], snap["sheet_id"], snap["start"], snap["end"], snap["text"])
            else:
                text = lyr()
                try:
                    a, b = int(S.sel[0]), int(S.sel[1])
                except (TypeError, ValueError, IndexError):
                    a, b = 0, 0
                if 0 <= a < b <= len(text):
                    _chat_detach["key"] = (S.song.id, S.song.book.active_id, a, b, text[a:b])
        refresh_chat_ctx()

    chat_ctx, _chat_sel_apply = CS.make_chat_selection_bar(
        detach_chat_selection, accent_soft=C["accent_soft"], text1=C["text1"], text2=C["text2"], border="#3A6294")

    def refresh_chat_ctx():
        """Show the slice the open song and sheet would attach, and nothing when that slice was dismissed or the song changed."""
        _has, text, _a, _b = _chat_selection_now()
        for ctl in _chat_sel_apply(text):
            safe_update(ctl)

    COLOURS = {"red": C["red"], "green": C["green"], "amber": C["amber"], "blue": C["accent"], "purple": "#A78BFA", "pink": "#F472B6", "gray": C["text3"]}
    RICH_RE = re.compile(r"\{(red|green|amber|blue|purple|pink|gray|b)\}(.*?)\{/\}|\*\*(.+?)\*\*", re.DOTALL)

    def rich_text(text: str, size: int, color: str):
        """A chat answer with the colour and bold the AI wrote ({red}word{/}, {b}word{/}, **word**). Unknown or unclosed marks are removed, never shown; the text itself is never changed."""
        text = SE.plain_symbols(text)
        text = re.sub(r"(?m)^[ \t]*#{1,4}[ \t]+(.+?)[ \t]*$", r"**\1**", text)           # "### Heading" -> bold line
        text = re.sub(r"(?m)^([ \t]*)[*\-][ \t]+(?=\S)", r"\1• ", text)                  # "* item" -> "• item" (a lone * is not shown)
        text = re.sub(r"`([^`\n]+)`", r"{blue}\1{/}", text)                              # `word` -> the word in colour, without the backticks
        spans, pos = [], 0
        for mt in RICH_RE.finditer(text):
            if mt.start() > pos:
                spans.append(ft.TextSpan(text[pos:mt.start()]))
            if mt.group(3) is not None or mt.group(1) == "b":
                spans.append(ft.TextSpan(mt.group(3) if mt.group(3) is not None else mt.group(2), style=ft.TextStyle(weight=ft.FontWeight.BOLD)))
            else:
                spans.append(ft.TextSpan(mt.group(2), style=ft.TextStyle(color=COLOURS[mt.group(1)], weight=ft.FontWeight.W_600)))
            pos = mt.end()
        spans.append(ft.TextSpan(text[pos:]))
        out = []
        for sp in spans:
            sp.text = SE.strip_colour(sp.text)                      # a mark that was not closed or not understood is dropped
            for part in chat_tags.line_ref_parts(sp.text):                 # L004 -> "แถวที่ 4": a link that selects that line in the editor
                if isinstance(part, str):
                    out.append(ft.TextSpan(part, style=sp.style))
                else:
                    label, a_, b_ = part
                    out.append(ft.TextSpan(label, style=ft.TextStyle(color=C["accent"], weight=ft.FontWeight.W_600, decoration=ft.TextDecoration.UNDERLINE),
                                           on_click=lambda ev, a=a_, b=b_: goto_file_line(a, b)))
        return ft.Text(spans=out, size=size, color=color, selectable=True)

    def ref_text(text: str, prefix: str = "", **kw):
        """Report text: a line id the AI wrote (L25, L25-L26) is shown as "แถวที่ 25" / "แถวที่ 25-26" and opens that line in the editor, like the chat. Text with no line id is an ordinary Text (so it is translated as before).
        `prefix` is a label before the text ("Why: "): translated, never part of a link."""
        text = text or ""
        parts = chat_tags.line_ref_parts(text)
        if not any(not isinstance(x, str) for x in parts):
            return ft.Text(prefix + text, **kw)
        spans = [ft.TextSpan(I18N.tr(prefix))] if prefix else []
        for part in parts:
            if isinstance(part, str):
                spans.append(ft.TextSpan(part))
            else:
                label, a_, b_ = part
                spans.append(ft.TextSpan(label, style=ft.TextStyle(color=C["accent"], weight=ft.FontWeight.W_600, decoration=ft.TextDecoration.UNDERLINE),
                                         on_click=lambda ev, a=a_, b=b_: goto_file_line(a, b)))
        return ft.Text(spans=spans, **kw)

    def chat_markdown(text: str):
        """Songwriter replies. Headings, bold and a comparison table. Links are not opened."""
        body = chat_tags.line_refs_markdown(SWT.prepare_chat_markdown(text))          # L004 -> a link "แถวที่ 4" that selects that line in the editor
        style = ft.TextStyle(size=13, color=C["text1"])
        return ft.Markdown(body, selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_FLAVORED, auto_follow_links=False, shrink_wrap=True, on_tap_link=on_chat_link,
                           md_style_sheet=ft.MarkdownStyleSheet(
                               a_text_style=ft.TextStyle(size=13, color=C["accent"], decoration=ft.TextDecoration.UNDERLINE),
                               p_text_style=style,
                               strong_text_style=ft.TextStyle(size=13, color=C["text1"], weight=ft.FontWeight.BOLD),
                               h2_text_style=ft.TextStyle(size=15, color=C["text1"], weight=ft.FontWeight.W_600),
                               h3_text_style=ft.TextStyle(size=14, color=C["text1"], weight=ft.FontWeight.W_600),
                               table_head_text_style=ft.TextStyle(size=12, color=C["text1"], weight=ft.FontWeight.W_600),
                               table_body_text_style=ft.TextStyle(size=12, color=C["text1"])))

    def bubble(i: int, m: M.ChatMessage):
        c = _bubble(i, m)
        c.key = "msg_" + m.id                                    # so the list can scroll to the TOP of a new answer
        return c

    def _bubble(i: int, m: M.ChatMessage):
        sid = S.song.id if S.song else ""
        if m.role == "user":
            return ft.Row([ft.Container(ft.Text(m.text, size=13, color="#FFFFFF", selectable=True), bgcolor=C["accent"], padding=po(12, 8, 12, 8), border_radius=12)],
                          alignment=ft.MainAxisAlignment.END, wrap=True)             # wrap=True: a long message is wrapped inside the list's width (in a plain Row it ran off the right edge)
        if m.role == "note":
            return ft.Text(m.text, size=11, color=C["amber"], selectable=True)
        who = SP.name_of(SP.owner(m.specialist)) if m.specialist or not m.action else ""
        prose = chat_markdown(m.text) if (not m.action and SP.owner(m.specialist) == "songwriter") else rich_text(m.text, 13, C["text1"])
        body = ([ft.Text("AI — " + I18N.tr(who), size=10, weight=ft.FontWeight.BOLD, color=C["accent"])] if who else []) + [prose]
        if m.action:                                            # a result of a Workspace -> AI Actions button: say which action, and on which part of the song
            lab = (m.action_req or {}).get("label") or AC.AI_ACTIONS.get(m.action, {}).get("label", "")
            body = [ft.Text("AI ACTION RESULT", size=11, weight=ft.FontWeight.BOLD, color=C["amber"]), ft.Text(lab, size=11, color=C["text2"], selectable=True)] + body
            if m.proposal:
                note = {"all": "This proposal covers the ENTIRE song: Apply replaces all of it (Undo brings it back).",
                        "insert": "Only new lines: Apply adds them after that line and replaces nothing."}.get(m.scope, "")
                if note:
                    body.append(ft.Text(note, size=11, color=C["amber"]))
        if m.edits:
            body.append(chat_review_row(m))
        if m.caption or m.settings or (m.settings_info or {}).get("groove"):
            body.append(caption_card(m, sid))
        if m.hints:
            body.append(hint_card(m.hints))
        if m.rejected:
            body.append(ft.Row([ft.TextButton("Show what the AI wrote (not applicable)", icon=ft.Icons.VISIBILITY_OUTLINED, on_click=lambda e, t=m.rejected: show_rejected(t))], spacing=4))
        if m.notes:
            body.append(ft.Text("ⓘ " + "  ·  ".join(m.notes), size=10, color=C["text3"], selectable=True))
        if m.proposal:
            body.append(ft.Container(ft.Text(m.proposal, size=12, color=C["text1"], selectable=True, font_family="Consolas"), bgcolor=C["field"], padding=10, border_radius=8,
                                     border=ft.Border.all(1, C["border"])))
            if m.applied:
                body.append(ft.Text("✓ Applied", size=11, color=C["green"]))
            body.append(ft.Row([ft.IconButton(ft.Icons.CHECK_ROUNDED, icon_size=18, icon_color="#FFFFFF", bgcolor=C["accent"], width=34, height=34, padding=0, tooltip="Apply to Editor",
                                                  on_click=lambda e, k=m.id, s=sid: apply_proposal(k, s), disabled=m.applied),
                                ibtn(ft.Icons.CONTENT_COPY_ROUNDED, "Copy", lambda e, t=m.proposal: page.run_task(copy_text, t, "Proposal")),
                                ibtn(ft.Icons.REFRESH_ROUNDED, "Regenerate", lambda e, k=m.id, s=sid: page.run_task(regenerate, k, s))], spacing=4))
        else:                                                   # a hand-off: the answer names a teammate -> one button takes the user there (the question is put in the message box; nothing is sent)
            mine = SP.owner(m.specialist)
            named = [t for t in SP.CHAT_IDS if t != mine and (SP.name_of(t) in m.text or I18N.tr(SP.name_of(t)) in m.text)]
            if named:
                body.append(ft.Row([ft.TextButton(f"Ask {SP.name_of(t)}", icon=ft.Icons.ARROW_FORWARD_ROUNDED, on_click=lambda e, t=t, k=i: refer_to(t, k)) for t in named], spacing=4, wrap=True))
        return ft.Container(ft.Column(body, spacing=6), bgcolor=C["panel2"], padding=po(12, 8, 12, 8), border_radius=12)

    cap_busy = {"on": False}

    def caption_card(m, song_id: str):
        """A Music Caption the Music Producer wrote in its CAPTION block (already checked): shown as text with Preview / Use in Music Style / Copy. Nothing changes until the user accepts the preview."""
        body = [ft.Text("Music Caption", size=10, weight=ft.FontWeight.BOLD, color=C["text3"]), ft.Text(m.caption, size=12, color=C["text1"], selectable=True)] if m.caption else []
        if not m.caption:
            pass
        elif m.caption_state == "applied":
            body.append(ft.Text("✓ Used in Music Style (the previous style is kept in the style History).", size=11, color=C["green"]))
        elif m.sheet_id and S.song and m.sheet_id != S.song.book.active_id:
            body.append(ft.Text("This caption belongs to another sheet. Open that sheet to use it.", size=11, color=C["amber"]))
        else:
            body.append(ft.Row([ft.FilledButton("Use in Music Style", tooltip="Preview this caption, then put it in Advanced style (lyrics and other settings stay)", on_click=lambda ev, k=m.id: caption_preview(k),
                                                style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF")),
                                ft.TextButton("Copy", tooltip="Copy the caption", on_click=lambda ev, t=m.caption: page.run_task(copy_text, t, "Caption"))], spacing=6, wrap=True))
        body.extend(settings_section(m, song_id))
        return ft.Container(ft.Column(body, spacing=4), bgcolor=C["panel"], border_radius=8, padding=po(10, 8, 10, 8))

    SET_LABEL = {"bpm": "BPM", "keyscale": "Key", "timesignature": "Time Signature"}
    set_ticks = {}                                          # message id -> the settings ticked in its card (a view choice, not saved)

    def set_show(name: str, v) -> str:
        if name == "timesignature":
            return next((lbl for k, lbl in TIMESIGS if k == str(v)), str(v))
        return "" if v in (None, "") else str(v)

    def settings_section(m, song_id: str) -> list:
        """Suggested Music Settings inside the caption card: current value next to the suggested one, a tick for each, Apply Selected Settings. A recommendation from the lyrics and the brief, never a reading of audio."""
        info = m.settings_info or {}
        if not (m.settings or info.get("groove")):
            return []
        applied = (m.settings_state or {}).get("applied") or {}
        body = [ft.Divider(height=6, color=C["border"]), ft.Text("Suggested Music Settings (a recommendation from the lyrics, not detected from audio)", size=10, weight=ft.FontWeight.BOLD, color=C["text3"])]
        here = bool(S.song) and (not m.sheet_id or m.sheet_id == S.song.book.active_id)
        cur = S.song.settings if S.song else None
        ticks = set_ticks.setdefault(m.id, {n for n, v in m.settings.items() if cur is None or getattr(cur, n, None) != v})
        for name in ("bpm", "keyscale", "timesignature"):
            if name not in m.settings:
                continue
            now_v = set_show(name, getattr(cur, name, "")) if cur else ""
            new_v = set_show(name, m.settings[name])
            done = name in applied
            same = bool(cur) and getattr(cur, name, None) == m.settings[name]
            cb = ft.Checkbox(value=name in ticks and not done, disabled=done or not here, on_change=lambda ev, n=name, k=m.id: (set_ticks[k].add(n) if ev.control.value else set_ticks[k].discard(n)))
            tail = "  ✓ Applied" if done else ("  (already set)" if same else "")
            body.append(ft.Row([cb, ft.Text(SET_LABEL[name], size=12, color=C["text2"], width=110), ft.Text(f"{now_v or '-'}  →  {new_v}{tail}", size=12, color=C["green"] if done else C["text1"], selectable=True)], spacing=4))
        if info.get("groove"):
            body.append(ft.Text("Rhythm feel (advice, not a setting): " + info["groove"], size=11, color=C["text2"], selectable=True))
        if info.get("reason"):
            body.append(ft.Text("Reason: " + info["reason"], size=11, color=C["text2"], selectable=True))
        for f_, written, why in info.get("bad") or []:
            body.append(ft.Text(f"{SET_LABEL.get(f_, f_)} “{written[:30]}” was not offered: {why}.", size=10, color=C["amber"]))
        if m.settings:
            if not here:
                body.append(ft.Text("These settings belong to another sheet. Open that sheet to use them.", size=11, color=C["amber"]))
            else:
                btns = [ft.FilledButton("Apply Selected Settings", tooltip="Put the ticked values into Song Settings. The caption, lyrics and cues stay.", on_click=lambda ev, k=m.id: settings_apply(k),
                                        disabled=len(applied) == len(m.settings), style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))]
                if applied:
                    btns.append(ft.TextButton("Undo settings", tooltip="Put back the values from before Apply (a value you changed since stays)", on_click=lambda ev, k=m.id: settings_undo(k)))
                body.append(ft.Row(btns, spacing=6, wrap=True))
        return body

    def settings_apply(mid: str):
        msg = find_msg(mid)
        if msg is None or not msg.settings or not S.song:
            toast("Those settings no longer exist. Nothing was applied.", C["amber"]); return
        if msg.sheet_id and msg.sheet_id != S.song.book.active_id:
            toast("These settings were made for another sheet. Open that sheet to use them. Nothing was changed.", C["amber"]); return
        old = msg.settings_state or {}
        chosen = [n for n in ("bpm", "keyscale", "timesignature") if n in set_ticks.get(mid, ()) and n in msg.settings and n not in (old.get("applied") or {})]
        if not chosen:
            toast("Nothing is ticked. Nothing was applied.", C["amber"]); return
        changes = {}
        for n in chosen:
            v, why = TA.check_field(n, msg.settings[n])             # checked again, now, by the same rules as the controls
            if why:
                toast(f"{SET_LABEL[n]} {msg.settings[n]}: {why}. Nothing was applied.", C["amber"]); return
            changes[n] = v
        sid_, sheet_id = S.song.id, S.song.book.active_id
        previous = {n: getattr(S.song.settings, n) for n in changes}
        try:
            SH.apply_settings(S.song, sheet_id, changes)               # the same action the controls use: only these fields of this sheet
        except SH.SheetError as ex:
            toast(str(ex), C["amber"]); return
        msg.settings_state = {"applied": {**(old.get("applied") or {}), **changes}, "previous": {**(old.get("previous") or {}), **previous}}
        ST.save_chat(sid_, S.chat)
        mark_dirty(); build_settings(); rebuild_chat()
        toast("Music Settings updated: " + ", ".join(f"{SET_LABEL[n]} {set_show(n, v)}" for n, v in changes.items()) + ". The caption and lyrics were not changed.", C["green"])

    def settings_undo(mid: str):
        msg = find_msg(mid)
        if msg is None or not S.song or not (msg.settings_state or {}).get("applied"):
            toast("Nothing to undo.", C["amber"]); return
        if msg.sheet_id and msg.sheet_id != S.song.book.active_id:
            toast("Open the sheet these settings were applied to. Nothing was changed.", C["amber"]); return
        stt = msg.settings_state
        cur_d = {n: getattr(S.song.settings, n) for n in stt["applied"]}
        back, kept = AD.plan_restore(stt.get("previous") or {}, stt["applied"], cur_d)
        if back:
            try:
                SH.apply_settings(S.song, S.song.book.active_id, back)
            except SH.SheetError as ex:
                toast(str(ex), C["amber"]); return
        msg.settings_state = {}
        set_ticks.pop(mid, None)
        ST.save_chat(S.song.id, S.chat)
        mark_dirty(); build_settings(); rebuild_chat()
        toast("Settings put back." + (" Kept (changed since): " + ", ".join(SET_LABEL.get(k, k) for k in kept) if kept else ""), C["green"])

    def caption_check(text: str):
        """(caption, [warnings], error sentence or ''): the same validation as the Basic caption (length, format, Style Prompt limits, no lyrics) against the settings of the sheet now."""
        st = S.song.settings
        try:
            cap, warns = BC.check_caption(text, lyr(), st.vocal, st.energy, st.extra_tags)
            return cap, warns, ""
        except BC.CaptionError as ex:
            return "", [], str(ex)

    def caption_preview(mid: str):
        msg = find_msg(mid)
        if msg is None or not msg.caption or not S.song:
            toast("That caption no longer exists. Nothing was applied.", C["amber"]); return
        song_id, sheet_id = S.song.id, S.song.book.active_id
        if msg.sheet_id and msg.sheet_id != sheet_id:
            toast("This caption was made for another sheet. Open that sheet to use it. Nothing was changed.", C["amber"]); return
        st = S.song.settings
        current = (st.style_prompt or "").strip() if st.style_mode == "advanced" else ""
        tf = ft.TextField(value=msg.caption, multiline=True, min_lines=7, max_lines=12, text_size=13, border_color=C["border"], bgcolor=C["field"], border_radius=10, content_padding=po(12, 10, 12, 10), width=500)
        st_icon, st_text = ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, size=16, color=C["green"]), ft.Text("", size=11, expand=True)
        count = ft.Text("", size=10, color=C["text3"])
        status = ft.Row([st_icon, st_text, count], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START, width=500)
        ok = ft.FilledButton("Use in Music Style", icon=ft.Icons.CHECK_ROUNDED, style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))

        def revalidate(e=None):
            cap, warns, err = caption_check(tf.value or "")
            col = C["red"] if err else (C["amber"] if warns else C["green"])
            st_icon.name = ft.Icons.ERROR_OUTLINE_ROUNDED if err else (ft.Icons.WARNING_AMBER_ROUNDED if warns else ft.Icons.CHECK_CIRCLE_ROUNDED)
            st_icon.color = st_text.color = col
            st_text.value = err if err else ("Valid" + ("  ·  " + "  ".join(warns) if warns else ""))
            count.value = f"{len((tf.value or '').split())} words"
            ok.disabled = bool(err)
            ok.style = ft.ButtonStyle(bgcolor=C["border"] if err else C["accent"], color=C["text3"] if err else "#FFFFFF")
            safe_update(st_icon, st_text, count, ok)

        def apply_now(e):
            if cap_busy["on"] or msg.caption_state == "applied":
                return                                           # a second click: not applied twice
            if not S.song or S.song.id != song_id or S.song.book.active_id != sheet_id:
                toast("The song or sheet changed while the preview was open. Nothing was applied.", C["amber"]); page.pop_dialog(); return
            cap, warns, err = caption_check(tf.value or "")      # the text may have been edited: checked again before it goes anywhere
            if err:
                toast("Nothing was applied. " + err, C["amber"]); revalidate(); return
            cap_busy["on"] = True
            try:
                s_ = S.song.settings
                old = (s_.style_prompt or "").strip()
                if old:
                    M.add_prompt_history(s_, old)                # the style it replaces stays in History: it can be put back from there
                SH.apply_settings(S.song, sheet_id, {"style_mode": "advanced", "style_prompt": cap})        # only the style; lyrics, BPM, duration and the rest stay; Generate is not pressed
                msg.caption_state = "applied"
                ST.save_chat(song_id, S.chat)
                mark_dirty(); build_settings(); rebuild_history(); rebuild_chat()
            except SH.SheetError as ex:
                toast(str(ex), C["amber"]); return
            finally:
                cap_busy["on"] = False
            page.pop_dialog()
            toast("Music Style updated (Advanced). The previous style is in the style History. Generate was not pressed.", C["green"])

        ok.on_click = apply_now
        tf.on_change = revalidate
        def card(label: str, body):
            return ft.Container(ft.Column([ft.Text(label, size=10, weight=ft.FontWeight.W_600, color=C["text3"]), body], spacing=4), bgcolor=C["panel2"], border_radius=10, padding=po(12, 8, 12, 8), width=500)
        old_txt = ft.Text(current, size=12, color=C["text2"], selectable=True, max_lines=4, overflow=ft.TextOverflow.ELLIPSIS) if current else ft.Text("No style in Advanced yet (Basic or empty).", size=12, color=C["text3"], italic=True)
        page.show_dialog(ft.AlertDialog(title=ft.Row([ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, size=20, color=C["accent"]), ft.Text("Use in Music Style", size=17, weight=ft.FontWeight.BOLD)], spacing=8), bgcolor=C["panel"],
                                        content=ft.Container(width=520, content=ft.Column([
                                            card("Current style (Advanced)", old_txt),
                                            ft.Text("New caption (you can edit it)", size=11, weight=ft.FontWeight.W_600, color=C["text2"]), tf, status,
                                            ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, size=14, color=C["text3"]), ft.Text("Only the Music Style changes: the lyrics, BPM, duration and the other settings stay as they are. The previous style is kept in the style History.",
                                                                                                                              size=10, color=C["text3"], expand=True)], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START, width=500)], spacing=8, tight=True)),
                                        actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ok], actions_padding=po(16, 4, 16, 14)))
        revalidate()

    def hint_card(hints: list):
        """Music direction the AI described that is not one of the studio's cues: it cannot be written into a section tag, so it is shown as text to put into the Music Caption."""
        txt = ", ".join(hints)
        return ft.Container(ft.Column([ft.Text("Music direction: not a studio cue, so it is not applied. It can go into the Music Caption:", size=10, color=C["text3"]),
                                       ft.Text(txt, size=11, color=C["text2"], selectable=True),
                                       ft.TextButton("Copy", tooltip="Copy this direction", on_click=lambda e, t=txt: page.run_task(copy_text, t, "Music direction"))], spacing=2),
                            bgcolor=C["panel"], border_radius=8, padding=po(10, 6, 10, 6))

    def show_rejected(t: str):
        page.show_dialog(ft.AlertDialog(title=ft.Text("What the AI wrote (not applicable)"), bgcolor=C["panel"], content=ft.Container(ft.Column([ft.Text(t, size=12, selectable=True, font_family="Consolas")], scroll=ft.ScrollMode.AUTO), width=560, height=dlg_h(380)),
                                        actions=[ft.TextButton("Copy", on_click=lambda e: page.run_task(copy_text, t, "Text")), ft.TextButton("Close", on_click=lambda e: page.pop_dialog())]))

    def _short(text: str, n: int = 44) -> str:
        t = re.split(r"(?<=[.!?])\s", (text or "").strip().split(chr(10))[0])[0]
        return t if len(t) <= n else t[:n - 1].rsplit(" ", 1)[0] + "…"

    def edit_card(msg, e, n: int = 1, total: int = 1):
        """One suggested change of existing lines: a short title, what it says now, what it would become, why, and its own Preview / Apply / Copy / Ignore. Its state is read from the lyrics as
        they are now (an Undo makes it open again), so Apply on one card never closes the others."""
        spec = SP.owner(msg.specialist)
        same_sheet = not msg.sheet_id or (S.song and msg.sheet_id == S.song.book.active_id)
        state = SE.edit_state(e, lyr(), spec) if same_sheet else "other"
        item = RV.Item(e["id"], "edit", e.get("first", 1) or 1, e.get("last", 1) or 1, e["original"], e["replacement"], e.get("reason", ""))
        where = f"Line {e['first']}" if e.get("first") == e.get("last") else f"Lines {e.get('first')}-{e.get('last')}"
        if e.get("section"):
            where = f"{e['section']} · {where}"
        if e.get("target"):
            where = f"{e['target']} · {where}"
        alt = e.get("alt")                                      # several wordings for the SAME lines: each is its own option; applying one makes the others stale (Undo opens them again)
        kind = chat_tags.KIND_LABEL.get(e.get("kind") or "", "")
        heads = chat_tags.PREVIEW_HEAD.get(e.get("kind") or "", ("Now", "Change to"))
        title = (f"Option {e.get('label') or alt['n']} ({alt['n']}/{alt['of']})" if alt else f"Suggestion {n}" + (f"/{total}" if total > 1 else "")) + (f" — {_short(e['reason'])}" if e.get("reason") else "")
        if kind:
            title = kind + " — " + title
        conflict = bool(same_sheet and state == "open" and e.get("id") in chat_tags.conflicting_ids(msg.edits or [], lyr()))
        body = [ft.Row([ft.Text(title, size=12, weight=ft.FontWeight.BOLD, color=C["text1"], expand=True), ft.Text(where, size=10, color=C["text3"])], spacing=6),
                ft.Text(heads[0], size=10, color=C["text3"]), _rv_lyric_box(e["original"], C["red"]), ft.Text(heads[1], size=10, color=C["text3"]), _rv_lyric_box(e["replacement"], C["green"])]
        if e.get("reason"):
            body += [ft.Text("Why", size=10, color=C["text3"]), ft.Text(e["reason"], size=11, color=C["text2"], selectable=True)]
        if state == "applied":
            body.append(ft.Text("✓ Applied. Undo in the editor makes it available again.", size=11, color=C["green"]))
        elif state == "ignored":
            body.append(ft.Text("Ignored", size=11, color=C["text3"]))
        elif state == "other":
            body.append(ft.Text("This change belongs to another sheet. Open that sheet to apply it.", size=11, color=C["amber"]))
        elif state == "stale":
            body.append(ft.Text("Another option for these lines was applied, or the lines changed, so this one cannot be applied here. You can still copy the new wording." if alt else "The lines changed since this suggestion, so it cannot be applied here. You can still copy the new wording.", size=11, color=C["amber"]))
            body.append(ft.Row([ft.TextButton("Copy", tooltip="Copy the new wording", on_click=lambda ev, t=e["replacement"]: page.run_task(copy_text, t, "Replacement")),
                                ft.TextButton("Ignore", tooltip="Hide this suggestion", on_click=lambda ev, k=msg.id, x=e["id"]: ignore_chat_edit(k, x))], spacing=6))
        else:
            if conflict:
                body.append(ft.Text("This overlaps another suggestion. Reject one of them first. Nothing is applied until you do.", size=11, color=C["amber"]))
            body.append(ft.Row([ft.OutlinedButton("Preview", tooltip="See the lines before and after; nothing changes", on_click=lambda ev, i=item, k=msg.id, x=e["id"]: rv_preview(i, lambda: apply_chat_edit(k, x))),
                                ft.FilledButton("Apply", tooltip="Put the new wording in the lyrics (Undo brings the old back)", disabled=conflict, on_click=lambda ev, k=msg.id, x=e["id"]: apply_chat_edit(k, x),
                                                style=ft.ButtonStyle(bgcolor=C["border"] if conflict else C["accent"], color="#FFFFFF")),
                                ft.TextButton("Copy", tooltip="Copy the new wording", on_click=lambda ev, t=e["replacement"]: page.run_task(copy_text, t, "Replacement")),
                                ft.TextButton("Ignore", tooltip="Hide this suggestion", on_click=lambda ev, k=msg.id, x=e["id"]: ignore_chat_edit(k, x))], spacing=4, wrap=True))
        return ft.Container(ft.Column(body, spacing=4), bgcolor=C["panel"], border_radius=8, padding=po(10, 8, 10, 8))

    crv = {"mid": "", "body": None, "title": None}

    def _cr_open(m) -> list:
        """The suggestions of this answer that can still be applied now: [(edit, first line, last line)] in the order of the lyrics (the state is read from the lyrics as they are now)."""
        if not S.song or (m.sheet_id and m.sheet_id != S.song.book.active_id):
            return []
        text, spec, out = lyr(), SP.owner(m.specialist), []
        for e in (m.edits or []):
            if SE.edit_state(e, text, spec) != "open":
                continue
            span = SE.resolve_tagged_span(e, text)
            if isinstance(span, str):
                continue
            loc = span if span is not None else SE.locate_text(RV.Item(e["id"], "edit", e.get("first", 1) or 1, e.get("last", 1) or 1, e["original"], e["replacement"], e.get("reason", "")), text)
            if isinstance(loc, tuple):
                end_at = loc[0] if loc[1] == loc[0] else loc[1] - 1
                out.append((e, text.count(chr(10), 0, loc[0]) + 1, text.count(chr(10), 0, end_at) + 1))
        return sorted(out, key=lambda t: t[1])

    def chat_review_row(m):
        """Under an answer with suggestions: how many wait, and one button that opens them in the coloured review (instead of a card per suggestion)."""
        n_open = len(_cr_open(m))
        total = len(m.edits or [])
        done = sum(1 for e in m.edits if SE.edit_state(e, lyr(), SP.owner(m.specialist)) in ("applied", "ignored")) if S.song else 0
        if n_open:
            return ft.Row([ft.FilledButton(f"Review and apply ({n_open})…", icon=ft.Icons.DIFFERENCE_OUTLINED, tooltip="See every suggestion in the lyrics, red = now, green = suggested; Accept or Reject each",
                                           on_click=lambda e, k=m.id: chat_review(k), style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF")),
                           ft.Text(f"{done}/{total} handled" if done else f"{total} suggestion(s)", size=10, color=C["text3"])], spacing=8, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        if m.sheet_id and S.song and m.sheet_id != S.song.book.active_id:
            return ft.Text("These suggestions belong to another sheet. Open that sheet to review them.", size=11, color=C["amber"])
        return ft.Text(f"{total} suggestion(s): all handled (Undo in the editor makes an applied one available again).", size=11, color=C["text3"])

    def _cr_hunk(m, e, n0: int, n1: int, text_lines: list, with_old: bool):
        parts = []
        kind = e.get("kind") or ""
        heads = chat_tags.PREVIEW_HEAD.get(kind, ("Now", "Change to"))
        label = chat_tags.KIND_LABEL.get(kind, "")
        where = " · ".join(x for x in (label, e.get("section") or "", e.get("target") or "", (f"line {n0}" if n0 == n1 else f"lines {n0}-{n1}")) if x)
        if where:
            parts.append(ft.Text(where, size=10, color=C["paper_dim"]))
        full = chr(10).join(text_lines[n - 1] for n in range(n0, n1 + 1) if 0 < n <= len(text_lines))
        orig = e.get("original") or ""
        partial = orig not in (full, full.strip(chr(10))) and chr(10) not in orig
        blocked = e.get("id") in chat_tags.conflicting_ids(m.edits or [], lyr())
        if partial and with_old:
            line = text_lines[n0 - 1] if 0 < n0 <= len(text_lines) else ""
            at = line.find(orig) if orig else -1
            if at >= 0:
                bits = [ft.Text(str(n0), size=10, color=C["paper_dim"], width=22), ft.Text(line[:at], size=13, color=C["ink"]),
                        ft.Text(line[at:at + len(orig)], size=13, color=RD_RED), ft.Text(line[at + len(orig):], size=13, color=C["ink"])]
                parts.append(ft.Text(heads[0], size=10, color=C["paper_dim"]))
                parts.append(ft.Container(ft.Row(bits, spacing=0, wrap=True), bgcolor=ft.Colors.with_opacity(0.06, "#DC2626"), padding=po(4, 2, 4, 2), border_radius=3))
            else:
                parts.append(ft.Text(heads[0], size=10, color=C["paper_dim"]))
                parts.append(ft.Container(ft.Text(orig, size=13, color=RD_RED), bgcolor=ft.Colors.with_opacity(0.10, "#DC2626"), padding=po(4, 2, 4, 2), border_radius=3, border=ft.Border(left=ft.BorderSide(3, "#DC2626"))))
        elif with_old:
            parts.append(ft.Text(heads[0], size=10, color=C["paper_dim"]))
            old = [ft.Row([ft.Text(str(n), size=10, color=C["paper_dim"], width=22), ft.Text(text_lines[n - 1] if 0 < n <= len(text_lines) else "", size=13, color=RD_RED, expand=True)], spacing=6) for n in range(n0, n1 + 1)]
            parts.append(ft.Container(ft.Column(old, spacing=1), bgcolor=ft.Colors.with_opacity(0.10, "#DC2626"), padding=po(4, 2, 4, 2), border_radius=3, border=ft.Border(left=ft.BorderSide(3, "#DC2626"))))
        parts.append(ft.Text(heads[1], size=10, color=C["paper_dim"]))
        new = [ft.Row([ft.Container(width=22), ft.Text(x, size=13, color=RD_GREEN, weight=ft.FontWeight.W_500, expand=True)], spacing=6) for x in e["replacement"].split(chr(10))]
        parts.append(ft.Container(ft.Column(new, spacing=1), bgcolor=ft.Colors.with_opacity(0.12, "#16A34A"), padding=po(4, 2, 4, 2), border_radius=3, border=ft.Border(left=ft.BorderSide(3, "#16A34A"))))
        if blocked:
            parts.append(ft.Text("Accept is closed: this overlaps another suggestion. Reject one of them first.", size=10, color=C["amber"]))
        who = SP.owner(m.specialist)
        accept = ft.Text("Accept closed", size=10, color=C["amber"]) if blocked else _rd_btn("Accept", ft.Icons.CHECK_ROUNDED, lambda ev, k=m.id, x=e["id"]: cr_accept(k, x), True)
        foot = ft.Row([ft.Container(ft.Text(SP.name_of(who), size=9, weight=ft.FontWeight.W_600, color="#FFFFFF"), bgcolor=CC_SPEC_COL.get(who, C["text3"]), border_radius=6, padding=po(5, 0, 5, 1)),
                       ft.Text(e.get("reason", ""), size=10, color=C["paper_dim"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                       accept, _rd_btn("Reject", ft.Icons.CLOSE_ROUNDED, lambda ev, k=m.id, x=e["id"]: cr_reject(k, x), False)],
                      spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        return ft.Container(ft.Column([*parts, ft.Container(foot, padding=po(26, 1, 2, 0))], spacing=3), margin=po(0, 3, 0, 6))

    def cr_rebuild():
        m = find_msg(crv["mid"])
        text = lyr()
        tl = text.split(chr(10))
        items = _cr_open(m) if m is not None else []
        by_line: dict = {}
        for e, a, b in items:
            by_line.setdefault(a, []).append((e, b))
        rows, upto = [], 0
        for n in range(1, len(tl) + 1):
            if n in by_line:
                for k, (e, b) in enumerate(by_line[n]):
                    rows.append(_cr_hunk(m, e, n, b, tl, k == 0 or b != by_line[n][0][1]))
                upto = max(upto, max(b for _e, b in by_line[n]))
            elif n > upto:
                if not tl[n - 1].strip():
                    rows.append(ft.Container(height=10))
                else:
                    tag = RC.is_tag_line(tl[n - 1])
                    rows.append(ft.Container(ft.Row([ft.Text(str(n), size=10, color=C["paper_dim"], width=22), ft.Text(tl[n - 1], size=13, color=C["paper_dim"] if tag else C["ink"], expand=True)], spacing=6,
                                                    vertical_alignment=ft.CrossAxisAlignment.START), padding=po(4, 2, 4, 2)))
        if not items:
            rows.insert(0, ft.Text("No suggestions are waiting. Everything has been accepted, rejected or no longer matches the lyrics.", size=12, color=C["paper_dim"]))
        crv["body"].controls = rows
        crv["title"].value = f"{len(items)} suggestion(s) waiting" if items else "No suggestions waiting"
        safe_update(crv["body"], crv["title"])

    def cr_accept(mid: str, eid: str):
        apply_chat_edit(mid, eid)                                    # the same checks and the same one Undo step as the card had
        cr_rebuild()

    def cr_reject(mid: str, eid: str):
        ignore_chat_edit(mid, eid)
        cr_rebuild()

    def cr_all(accept: bool):
        skipped = set()
        for _ in range(100):                                         # an Accept can make an alternative for the same lines stale, so the list is read again every time
            m = find_msg(crv["mid"])
            items = [it for it in (_cr_open(m) if m is not None else []) if it[0]["id"] not in skipped]
            if not items:
                break
            e = items[0][0]
            if accept and m is not None and e["id"] in chat_tags.conflicting_ids(m.edits or [], lyr()):
                skipped.add(e["id"])                                 # overlapping suggestions stay open; Accept all does not reject them
                continue
            if accept:
                before = S.song.lyrics
                skipped.add(e["id"])                                 # one Accept per suggestion in a run, whatever the lyrics look like afterwards
                apply_chat_edit(crv["mid"], e["id"])
                if S.song.lyrics == before:
                    ignore_chat_edit(crv["mid"], e["id"])             # it cannot be applied: it is not offered again
            else:
                ignore_chat_edit(crv["mid"], e["id"])
        cr_rebuild()

    def chat_review(mid: str):
        if time.monotonic() < crv.get("until", 0.0):
            return                                                   # a second click while the first is still opening (clicks are handled one after the other): one dialog only
        crv["until"] = time.monotonic() + 30.0
        try:
            _chat_review_open(mid)
        finally:
            crv["until"] = time.monotonic() + 1.5                    # clicks that queued up during the opening are dropped; a later click works again

    def _chat_review_open(mid: str):
        m = find_msg(mid)
        if m is None or not S.song:
            toast("That answer no longer exists.", C["amber"]); return
        crv["mid"] = mid
        crv["title"] = ft.Text("", size=12, color=C["text2"])
        crv["body"] = ft.Column([], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
        t0 = time.perf_counter()
        cr_rebuild()
        t1 = time.perf_counter()
        log.info("review dialog: built %d controls in %.0f ms (before it is shown)", len(crv["body"].controls), (t1 - t0) * 1000)
        page.show_dialog(ft.AlertDialog(title=ft.Row([ft.Text("Review AI suggestions", size=18, weight=ft.FontWeight.BOLD), crv["title"]], spacing=12, vertical_alignment=ft.CrossAxisAlignment.END),
                                        bgcolor=C["panel"], content=ft.Container(width=int(max(420, min(820, page_w() - 120))), height=dlg_h(680, 0.82), bgcolor=C["paper"], border_radius=8, padding=po(8, 6, 8, 6), content=crv["body"]),
                                        actions=[ft.TextButton("Reject all", on_click=lambda e: cr_all(False)), ft.TextButton("Accept all", on_click=lambda e: cr_all(True)),
                                                 ft.FilledButton("Close", on_click=lambda e: page.pop_dialog(), style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))]))

    def _chat_edit(mid: str, eid: str):
        msg = find_msg(mid)
        e = next((x for x in (msg.edits if msg else []) if x.get("id") == eid), None)
        return msg, e

    def apply_chat_edit(mid: str, eid: str):
        """Apply ONE suggested change of a chat answer where its original text is now; the other changes of the same answer stay available."""
        msg, e = _chat_edit(mid, eid)
        if msg is None or e is None or not S.song:
            toast("That suggestion no longer exists. Nothing was applied.", C["amber"]); return
        if msg.sheet_id and msg.sheet_id != S.song.book.active_id:
            toast(f"This change was made for the sheet “{sheet_label(msg.sheet_id)}”. Open that sheet to apply it. Nothing was changed.", C["amber"]); return
        if e.get("song_id") and e["song_id"] != S.song.id:
            toast("This suggestion belongs to another song. Nothing was applied.", C["amber"]); return
        if e.get("id") in chat_tags.conflicting_ids(msg.edits or [], lyr()):
            toast("This overlaps another suggestion. Reject one of them first. Nothing was applied.", C["amber"]); return
        spec = SP.owner(msg.specialist)
        if SE.edit_state(e, lyr(), spec) != "open":
            toast("This change cannot be applied now (already applied, ignored, or the lines changed).", C["amber"]); rebuild_chat(); return
        got = SE.apply_edit(e, lyr(), spec)
        if isinstance(got, str):
            toast(got, C["amber"]); return
        new_text, sel = got
        set_lyrics(new_text, sel, refocus=False)
        e["status"], e["applied_hash"] = "applied", SE.text_hash_of(new_text)
        ST.save_chat(S.song.id, S.chat)
        TRC.log_event("apply", specialist=spec, edit=e["id"], original=e["original"], applied_text=e["replacement"], line_hint=e.get("first"))
        rebuild_chat()
        toast("Applied (Undo restores the previous lyrics)", C["green"])

    def ignore_chat_edit(mid: str, eid: str):
        msg, e = _chat_edit(mid, eid)
        if e is not None and S.song:
            e["status"] = "ignored"
            ST.save_chat(S.song.id, S.chat)
            rebuild_chat()

    def refer_to(spec_id: str, msg_index: int):
        """Follow a hand-off: open that specialist's chat and put the user's own last question in the message box. No AI call: the user sends it when they want."""
        q = next((m.text for m in reversed(S.chat[:msg_index]) if m.role == "user" and (SP.owner(m.specialist)) == (SP.owner(S.chat[msg_index].specialist))), "")
        set_spec(spec_id)
        chat_tf.value = q
        safe_update(chat_tf)

    def _chat_scrolled(e):
        """Where the user is in the chat. Only this decides whether a new answer may scroll the list: at the end -> follow; reading above -> stay where they are (a 'New message' button is shown)."""
        try:
            chat_scroll["pixels"], chat_scroll["max"] = float(e.pixels), float(e.max_scroll_extent)
            log.debug("chat scroll: pixels=%.0f max=%.0f", chat_scroll["pixels"], chat_scroll["max"])
            was = chat_scroll["at_end"]
            chat_scroll["at_end"] = (chat_scroll["max"] - chat_scroll["pixels"]) <= 48
            if chat_scroll["at_end"] and chat_new_chip.visible:
                chat_new_chip.visible = False
                safe_update(chat_new_chip)
            if chat_scroll["ctx"] is not None:
                chat_scroll["saved"][chat_scroll["ctx"]] = (chat_scroll["pixels"], chat_scroll["at_end"])
            _ = was
        except Exception:
            pass

    async def _chat_to_message(key: str):
        """Scroll so that the top of the message with this key is at the top of the chat list (a long answer is read from its first line; a short one ends up at the end as before)."""
        for wait in (0.1, 0.3):                                   # after the client has measured the new bubble
            await asyncio.sleep(wait)
            try:
                await chat_list.scroll_to(scroll_key=key, duration=0)
            except Exception as ex:
                log.debug("chat scroll_to(message) failed: %r", ex)
                await _chat_to_end()
                return
        await asyncio.sleep(0.25)                                 # the scroll events of the jump have arrived: is the rest of the answer below the visible part?
        below = (chat_scroll["max"] - chat_scroll["pixels"]) > 48
        if chat_new_chip.visible != below:
            chat_new_chip.visible = below                         # a long answer is read from its first line: the pill says there is more below (and that the AI has finished)
            safe_update(chat_new_chip)

    async def _chat_to_end(animated: bool = False):
        for wait in (0.08, 0.3):                                  # the client measures a new bubble a moment after it was added: a scroll sent at once stopped short of a long answer (only its first line showed), so it is sent again after the layout
            await asyncio.sleep(wait)
            try:
                await chat_list.scroll_to(offset=-1, duration=150 if animated else 0)
            except Exception as ex:
                log.debug("chat scroll_to(end) failed: %r", ex)
        chat_scroll["at_end"] = True
        if chat_new_chip.visible:
            chat_new_chip.visible = False
            safe_update(chat_new_chip)

    async def _chat_restore(pos):
        """After another specialist / sheet / song is shown: back to where the user was in THAT conversation (the end when it was never opened or was at the end)."""
        await asyncio.sleep(0.05)
        try:
            if pos is None or pos[1]:
                await chat_list.scroll_to(offset=-1)
            else:
                await chat_list.scroll_to(offset=max(0.0, pos[0]))
        except Exception:
            pass

    def _msg_sig(m_: "M.ChatMessage") -> tuple:
        """What a bubble shows: when it is unchanged its control is kept as it is."""
        spec = SP.owner(m_.specialist)
        es = tuple(SE.edit_state(e_, lyr(), spec) if (not m_.sheet_id or (S.song and m_.sheet_id == S.song.book.active_id)) else "other" for e_ in (m_.edits or []))
        return (m_.id, len(m_.text), m_.applied, len(m_.proposal), es, m_.caption_state)

    def rebuild_chat(force_end: bool = False):
        """Show the open specialist's conversation. A new message is APPENDED and a changed one replaces only its own bubble; the list is rebuilt from scratch only when another conversation is shown."""
        ctx = (S.song.id, S.song.book.active_id, S.spec) if S.song else None
        refresh_spec_dd()
        if not S.song or not any(spec_visible(x) for x in S.chat):
            chat_scroll["keys"] = []
            hidden = [x for x in S.chat if S.song and SP.owner(x.specialist) == S.spec and S.spec in SP.CHAT_IDS and old_message(x) and (not x.specialist or x.sheet_id in ('', S.song.book.active_id))] if S.song else []
            chat_list.controls = ([ft.TextButton(f"Show earlier messages ({len(hidden)})", icon=ft.Icons.HISTORY_ROUNDED, on_click=lambda e: (setattr(S, "show_old", True), rebuild_chat()),
                                                 style=ft.ButtonStyle(color=C["accent"]))] if hidden else []) + [ft.Text("Open or create a song to chat about its lyrics." if not S.song else
                                          ("This is a read-only archive of a former specialist's chat. Nothing of it is saved on this sheet; chats made on other sheets show when you open those sheets. Choose Songwriter AI to write or ask something new." if S.spec in SP.ARCHIVE_IDS else
                                           f"{SP.name_of(S.spec)}: {SP.BY_ID[S.spec].short}. Ask a question, ask for a proposal, or press Analyze Song and ask about the report. Nothing changes until you press Apply."), size=12, color=C["text3"])]
            chat_scroll["ctx"] = ctx
            safe_update(chat_list)
            refresh_chat_ctx()
            return
        vis = [(i, x) for i, x in enumerate(S.chat) if spec_visible(x)]
        keys = [_msg_sig(x) for _i, x in vis]
        old = chat_scroll["keys"]
        same_conv = chat_scroll["ctx"] == ctx and len(old) <= len(keys) and all(old[k][0] == keys[k][0] for k in range(len(old))) and len(chat_list.controls) == len(old)
        user_sent, grew = False, False
        if not same_conv:                                          # another conversation (or the first draw): build it and go back to where the user was in it
            if chat_scroll["ctx"] is not None and chat_scroll["ctx"] != ctx:
                pass
            chat_list.controls = [bubble(i, x) for i, x in vis]
            saved = chat_scroll["saved"].get(ctx)
            chat_scroll["ctx"], chat_scroll["keys"] = ctx, keys
            safe_update(chat_list)
            page.run_task(_chat_restore, saved)
        else:
            for k in range(len(old)):
                if old[k] != keys[k]:                              # only the bubble that changed (Apply / Ignore / a state that changed) is replaced
                    chat_list.controls[k] = bubble(vis[k][0], vis[k][1])
                    grew = grew or (vis[k][1].role != "user" and keys[k][1] > old[k][1])          # an answer that got longer (it was written while the user read above)
            added = vis[len(old):]
            for i, x in added:
                chat_list.controls.append(bubble(i, x))
                user_sent = user_sent or x.role == "user"
            chat_scroll["keys"] = keys
            at_end = chat_scroll["at_end"]
            safe_update(chat_list)
            if added and (force_end or user_sent or at_end):
                last = added[-1][1]
                if last.role != "user" and not force_end:
                    page.run_task(_chat_to_message, "msg_" + last.id)        # an answer arrived: its TOP is shown, so it is read from the beginning without scrolling back
                else:
                    page.run_task(_chat_to_end)                    # the user sent it, or was reading the end: show the newest
            elif (added or grew) and not at_end:
                chat_new_chip.visible = True                       # the user is reading older messages: do not take the scroll from them
                safe_update(chat_new_chip)
        refresh_chat_ctx()

    def append_chat(song_id: str, msg: M.ChatMessage):
        """Chat belongs to its song: a late answer goes to the song that asked, even if another song is open now."""
        if S.song and S.song.id == song_id:
            S.chat.append(msg)
            ST.save_chat(song_id, S.chat)
            rebuild_chat()
        else:
            sess = sessions.get(song_id)
            msgs = sess.chat if sess else ST.load_chat(song_id)      # an open tab keeps its own list, so nothing is lost when it is shown again
            msgs.append(msg)
            ST.save_chat(song_id, msgs)

    # ═══ AI SPECIALISTS: one chat and one session per specialist (per song and sheet), and the calls that use them ═══════════════════════════════════════════════════════════════
    # The chat messages live in the song's chat file (each tagged with its specialist and sheet); the session (prompt hash, generation, summary, reports, debug trail) lives in the song's
    # `specialists/` folder. The chat and the analysis of a specialist are ONE live conversation kept by the backend (local: the node's session id; Grok: messages in RAM), never replayed from disk (specialist_engine.prepare_live); the other tasks are single requests (specialist_engine.prepare).
    SPEC_ACTION = {"continue": "songwriter", "emotion": "songwriter", "phrasing": "songwriter", "translate": "songwriter", "singability": "songwriter"}      # Saved / custom prompts keep their old behaviour
    spec_sessions: dict = {}
    spec_locks: dict = {}
    _open_task: dict = {}                                       # (song, sheet, specialist) -> the next message gets the fact that a task waits for the user's lyrics (the model decides what to do with it)
    chat_who = ft.Text("", size=11, color=C["accent"], height=16)             # always in the layout (empty when idle): showing / hiding it would move the chat list and its scroll position

    def get_spec_session(song_id: str, sheet_id: str, spec: str):
        k = (song_id, sheet_id, spec)
        if k not in spec_sessions:
            spec_sessions[k] = SE.load_session(song_id, sheet_id, spec)
        return spec_sessions[k]

    ctx_warned: set = set()                                     # (song, sheet, specialist, generation) whose "the conversation is getting long" heads-up was shown

    def n_ctx_for(backend: str) -> int:
        """The context size to plan with: read from the local workflow; a conservative 32768 for Grok (the real limit is larger, but the budget should not depend on a number we do not read)."""
        if backend == "grok":
            return LC.grok_context()
        try:
            return LC.context_size(LLM_DIR / backend.split(":", 1)[1])
        except Exception:
            return 8192

    def spec_extra(spec: str) -> str:
        """What the program adds to a specialist's own prompt in a CHAT request: one chat contract for the whole conversation, plus craft notes or the cues the studio supports."""
        extra = SE.chat_contract(spec) + chr(10) * 2 + SE.CONVERSATION_RULES + chr(10) * 2 + SE.team_block(spec) + chr(10) * 2 + AK.knowledge(("lyrics_health",))
        if spec == "songwriter":
            extra += "\n\n" + SONGWRITING_CRAFT + "\n\n" + SWT.system_block()
        elif SP.is_producer(spec):
            extra += chr(10) + SE.supported_cues_text() + chr(10) * 2 + SE.ARCHITECT_CHAT_RULE
        return extra

    def spec_llm(system: str, user: str, backend: str, host: str, temperature: "float | None") -> str:
        if backend == "grok":
            g = LC.grok_creds()
            return LC.ask_grok(g["key"], g["model"], system, user, temperature=temperature)
        return LC.ask_local(host, LLM_DIR / backend.split(":", 1)[1], system, user, temperature=temperature)

    def spec_llm_live(prep, backend: str, host: str, temperature: "float | None") -> str:
        """One request of a specialist's live conversation (specialist_engine.prepare_live): Grok gets the earlier messages with the new one; the local node keeps them under the session id and gets only the new one."""
        if backend == "grok":
            g = LC.grok_creds()
            return LC.ask_grok(g["key"], g["model"], prep.system, prep.user, temperature=temperature, history=prep.history)
        return LC.ask_local(host, LLM_DIR / backend.split(":", 1)[1], prep.system, prep.user, temperature=temperature, session=prep.node)

    def spec_ask(spec: str, temperature: "float | None" = None, task: str = ""):
        """An `ask(system, user)` for the engines: the engine's own prompt becomes the TASK of this specialist, and the request goes out as one request of its session. Everything it needs from the
        UI is captured NOW (song, sheet, backend, chat), so the call can run in a worker thread. One request at a time goes into one session."""
        song_id, sheet_id = S.song.id, S.song.book.active_id
        backend, host, chat, h = S.backend, S.host, (list(S.chat) if task in ("", "analyze") else []), LA.text_hash(lyr())
        try:
            model, _p = an_identity(backend)
        except Exception:
            model = ""
        sid_ = SP.analysis_id(spec) if task == "analyze" else spec          # the Music Producer's analysis has a session of its own (its own history and file); the chat's is never mixed in
        lock = spec_locks.setdefault((song_id, sheet_id, sid_), threading.Lock())

        def ask(system: str, user: str) -> str:
            with lock:
                sess = get_spec_session(song_id, sheet_id, sid_)
                live_task = task == "analyze"                              # the specialist's analysis and its chat are ONE conversation (live session); the other tasks are single requests
                if live_task:
                    prep = SE.prepare_live(sess, backend, model, system, user, n_ctx_for(backend), "", h, include_report=False, mode="report")       # ValueError: the prompt file is not valid, nothing is sent; an analysis never carries the old report
                else:
                    prep = SE.prepare(sess, chat, system, user, n_ctx_for(backend), "", h, backend, model, lite=(task in SE.LITE_TASKS))       # ValueError: the prompt file is not valid, nothing is sent
                t0 = time.time()
                try:
                    raw = spec_llm_live(prep, backend, host, temperature) if live_task else spec_llm(prep.system, prep.user, backend, host, temperature)
                    if live_task:
                        SE.commit_live(prep, raw)
                    SE.add_debug(sess, backend=backend, model=model, est_in=prep.est_in, est_out=LC.estimate_tokens(raw), sec=round(time.time() - t0, 1), status="ok", task=task, new_generation=prep.new_generation)
                    return raw
                except Exception as ex:
                    SE.add_debug(sess, backend=backend, model=model, est_in=prep.est_in, sec=round(time.time() - t0, 1), status="failed", task=task, error=str(ex)[:160])
                    raise
                finally:
                    SE.save_session(sess)
        return ask

    def spec_visible(m: "M.ChatMessage") -> bool:
        """Is this chat message part of the open specialist's conversation on the open sheet? A message from before specialists has no specialist: it belongs to the default chat (the Songwriter AI), on every sheet."""
        who = SP.owner(m.specialist)
        if who != S.spec:
            return False
        if S.spec in SP.CHAT_IDS and not S.show_old and old_message(m):
            return False                                          # a message of an earlier run: not shown (kept in the file; "Show earlier messages" brings it back)
        return not (m.specialist and S.song and m.sheet_id not in ("", S.song.book.active_id))

    def old_message(m: "M.ChatMessage") -> bool:
        return bool(m.ts) and m.ts < RUN_START

    def set_spec(spec: str):
        """Talk to another specialist: only the visible conversation changes. No LLM call, and the session of the specialist that was open is kept. The former specialists (SP.ARCHIVE_IDS) can be opened to READ
        their saved chat; nothing can be sent to them, and the choice is not remembered for the next start."""
        if spec not in SP.CHAT_IDS and spec not in SP.ARCHIVE_IDS:
            return
        S.spec = spec
        if spec in SP.CHAT_IDS:
            cfg["spec"] = spec
            _save_cfg(cfg)
        spec_dd.value = spec
        refresh_spec_dd()
        rebuild_chat()

    def archive_note() -> bool:
        """True (after a message to the user) when the open conversation is a read-only archive of a former specialist."""
        if S.spec in SP.ARCHIVE_IDS:
            toast(f"This is the saved chat of the former {SP.name_of(S.spec)} (read-only). Choose Songwriter AI to write or ask something new.", C["amber"])
            return True
        return False

    def open_prompts_folder():
        try:
            SP.ensure_user_copies()
            os.startfile(str(SP.USER_DIR))
        except Exception as ex:
            toast(f"Cannot open the prompts folder: {ex}", C["amber"])

    def toggle_trace(on=None):
        """The log keeps your lyrics and the AI's raw answers on this computer (never sent anywhere). It can be switched off."""
        TRC.ENABLED = (not TRC.ENABLED) if on is None else bool(on)
        cfg["spec_trace"] = TRC.ENABLED
        _save_cfg(cfg)
        toast("The specialist log is ON: it keeps your lyrics and the AI answers on this computer." if TRC.ENABLED else "The specialist log is OFF. Nothing more is written.", C["green"] if TRC.ENABLED else C["amber"])

    def delete_trace():
        def do():
            n = 0
            for p_ in (TRC.trace_path(), TRC.trace_path().with_name("specialist_trace.1.jsonl")):
                try:
                    p_.unlink(missing_ok=True); n += 1
                except OSError:
                    pass
            toast("The specialist log was deleted.", C["green"])
        confirm("Delete the specialist log", "Delete the file that keeps your lyrics and the AI's raw answers for debugging?", "Delete", do, danger=True)

    def open_trace_folder():
        """The trace of what the specialists were asked and answered (specialist_trace.jsonl): for finding out why a proposal did not arrive."""
        try:
            TRC.trace_path().parent.mkdir(parents=True, exist_ok=True)
            os.startfile(str(TRC.trace_path().parent))
        except Exception as ex:
            toast(f"Cannot open the log folder: {ex}", C["amber"])

    def reload_prompts():
        bad = [f"{SP.name_of(i)}: {' '.join(p.errors)}" for i, p in SP.reload_all().items() if not p.ok]
        toast("The prompts were reloaded. A changed prompt starts a new session generation for that specialist." if not bad else "Reloaded, but some prompts are not valid and will not be sent: " + " | ".join(bad)[:300],
              C["green"] if not bad else C["amber"])

    def show_effective_prompt():
        try:
            text, p = SP.effective_system(S.spec, spec_extra(S.spec), "chat")
            rtext, _p = SP.effective_system(S.spec, SE.analyze_task(S.spec), "report")
        except ValueError as ex:
            toast(str(ex), C["amber"]); return
        fmt = ("written in COMMON / CHAT / REPORT sections: the chat gets COMMON + CHAT, Analyze Song gets COMMON + REPORT" if p.sections
               else "legacy format (no mode sections): the whole file is sent in every mode")
        page.show_dialog(ft.AlertDialog(title=ft.Text(f"Effective system prompt: {SP.name_of(S.spec)}"), bgcolor=C["panel"], content=ft.Container(width=640, height=dlg_h(460), content=ft.Column([
            ft.Text(f"prompt hash {p.hash} · file {p.path}", size=11, color=C["text3"]), ft.Text("Format: " + fmt, size=11, color=C["amber"] if not p.sections else C["text3"]),
            ft.Text("CHAT (system prompt of the conversation)", size=11, weight=ft.FontWeight.BOLD, color=C["text2"]), ft.Text(text, size=11, selectable=True, font_family="Consolas", color=C["text1"]),
            ft.Divider(height=8, color=C["border"]),
            ft.Text("ANALYZE SONG (system prompt of the report)", size=11, weight=ft.FontWeight.BOLD, color=C["text2"]), ft.Text(rtext, size=11, selectable=True, font_family="Consolas", color=C["text1"])], scroll=ft.ScrollMode.AUTO, spacing=6)),
            actions=[ft.TextButton("Close", on_click=lambda e: page.pop_dialog())]))

    def reset_prompt_confirm():
        def do():
            try:
                bak = SP.reset(S.spec)
                toast(f"The default prompt of {SP.name_of(S.spec)} was put back." + (f" Your file was kept as {Path(bak).name}." if bak else ""), C["green"])
            except Exception as ex:
                toast(f"Could not reset the prompt: {ex}", C["red"])
        confirm("Reset prompt", f"Put the default prompt of {SP.name_of(S.spec)} back? Your current file is kept as a backup next to it.", "Reset", do)

    def spec_options() -> list:
        """The specialists to talk to. The former specialists are not listed any more (their saved conversations stay in the song's files)."""
        return [ft.dropdown.Option(i, SP.name_of(i)) for i in SP.CHAT_IDS]

    spec_dd = ft.Dropdown(value=S.spec, options=spec_options(), on_select=synced(lambda e: set_spec(e.control.value)), expand=True, **field_style(text_size=12))

    def refresh_spec_dd():
        try:
            opts = spec_options()
            changed = False
            if [o.key for o in opts] != [o.key for o in spec_dd.options]:
                spec_dd.options, changed = opts, True
            if spec_dd.value != S.spec:
                spec_dd.value, changed = S.spec, True
            if changed:                                          # an update of the dropdown on every chat redraw is not needed (and a control that is redrawn can pull the keyboard focus away from the message box)
                safe_update(spec_dd)
        except Exception as ex:
            dbg_once("spec dropdown", ex)

    async def ask_ai(instruction: str):
        """One message to the open specialist: its prompt, the summary of its earlier generations, its conversation on this sheet and its latest report go with it (specialist_engine.prepare)."""
        if not S.song:
            return
        if archive_note():
            return
        if S.chat_busy_song:
            toast("The AI is still answering"); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        song, spec = S.song, S.spec
        sheet0 = song.book.active_id                            # the proposal belongs to THIS sheet, whatever sheet is open when the answer arrives
        text = song.lyrics
        has_sel, selected, a, b = _chat_selection_now()
        meta_s = {"genre": song.settings.genre, "mood": song.settings.mood, "language": K.detect_language(text) if (song.settings.language or "auto") == "auto" else song.settings.language, "bpm": song.settings.bpm}
        if SP.is_producer(spec):
            meta_s.update({"energy": song.settings.energy, "vocal": song.settings.vocal})
            lines = (text.count(chr(10), 0, a) + 1, text.count(chr(10), 0, b) + 1) if has_sel else None
            user_msg = SE.architect_user_message(song.title, meta_s, text, instruction, lines, selected if has_sel else "")
        else:
            user_msg = LC.build_user_message(song.title, meta_s, text, selected, instruction, [], LC.selection_where(text, a, b) if has_sel else "")
        quick = SE.small_talk_reply(instruction)                    # thanks / a greeting: answered at once, nothing is sent to the AI (no analysis, no cost)
        if quick:
            append_chat(song.id, M.ChatMessage("assistant", quick, sheet_id=sheet0, specialist=spec))
            TRC.log_event("chat", specialist=spec, song_id=song.id, sheet_id=sheet0, instruction=instruction, steps=["small talk answered without the AI"], reply_shown=quick, proposal_offered=False)
            return
        hist = S.chat[:-1] if (S.chat and S.chat[-1].role == "user" and S.chat[-1].text == instruction) else list(S.chat)     # the message just typed is in the request itself
        if not S.show_old:
            hist = [m for m in hist if not old_message(m)]                  # a new run starts a new chat session: earlier messages are neither shown nor counted as 'on screen'
        task_key = (song.id, sheet0, spec)
        open_note = spec == "songwriter" and _open_task.pop(task_key, False)
        try:
            model, _p = an_identity(S.backend)
        except Exception:
            model = ""
        backend, host = S.backend, S.host
        sess = get_spec_session(song.id, sheet0, spec)
        try:
            report = get_spec_session(song.id, sheet0, SP.analysis_id(spec)).latest_report()
        except Exception:
            report = None
        live_now = SE.get_live(sess, SE.live_kind(backend), model or "")
        going = (not SP.is_producer(spec)) and bool(report) and not live_now.report_sent       # the first live message already carries the Songwriter report
        block = SE.suggestion_attachment(spec, report, LA.text_hash(text), going)
        if block:
            user_msg += chr(10) * 2 + block                                          # findings only: the model may propose, the user still previews. No extra LLM call.
        if open_note:
            user_msg = SE.OPEN_TASK_NOTE + chr(10) * 2 + user_msg                    # a fact for this message only; the model decides whether the message is the awaited lyrics or something new
        S.chat_busy_song = song.id
        chat_who.value = f"{SP.name_of(spec)} is answering…"
        chat_busy.visible = True; safe_update(chat_busy, chat_who)
        loop = asyncio.get_running_loop()
        lock = spec_locks.setdefault((song.id, sheet0, spec), threading.Lock())
        sid = song.id

        meta: dict = {}
        tool_ctx = None
        if spec == "songwriter":
            song_sess = sessions.get(song.id)
            tool_ctx = SWT.SongContext(
                lyrics=text, title=song.title or "", genre=song.settings.genre or "", mood=song.settings.mood or "",
                language=str(meta_s.get("language") or ""), bpm=float(song.settings.bpm) if song.settings.bpm else None,
                timesig=str(song.settings.timesignature or ""), duration=float(song.settings.duration) if song.settings.duration else None,
                corrections=dict(song_sess.an_corr) if song_sess is not None else {})

        def work():
            with lock:                                           # one request at a time into one session
                prep = SE.prepare_live(sess, backend, model, "", user_msg, n_ctx_for(backend), spec_extra(spec), LA.text_hash(text))      # the live conversation: only this message travels (Grok: with its RAM history)
                meta.update(est_in=prep.est_in, generation=prep.generation, new_generation=prep.new_generation, prompt_hash=prep.prompt_hash, usage=prep.usage)
                if prep.first and SE.chat_history(hist, spec, sheet0):
                    meta["session_note"] = "A new AI session started, so the AI does not see the earlier messages on screen."
                t0 = time.time()
                try:
                    if spec == "songwriter":
                        sent = {"n": 0}

                        def ask(next_user, history):
                            if sent["n"]:
                                prep.live.started = True          # the next tool round stays on this session id
                            sent["n"] += 1
                            return spec_llm_live(replace(prep, user=next_user, history=history), backend, host, None)

                        try:
                            result = SWT.run_tool_loop(ask, tool_ctx, prep.user, prep.history)
                        except SWT.ToolLoopError as ex:
                            if ex.turns:
                                SE.commit_live_turns(prep, ex.turns)
                            raise ex.original
                        SE.commit_live_turns(prep, result.turns)
                        meta["tool_steps"] = result.steps
                        raw = I18N.tr(result.visible) if result.visible == SWT.LIMIT_NOTE else result.visible
                    else:
                        raw = spec_llm_live(prep, backend, host, None)
                        SE.commit_live(prep, raw)
                    SE.add_debug(sess, backend=backend, model=model, est_in=prep.est_in, est_out=LC.estimate_tokens(raw), sec=round(time.time() - t0, 1), status="ok", task="chat", new_generation=prep.new_generation)
                    return raw
                except Exception as ex:
                    SE.add_debug(sess, backend=backend, model=model, est_in=prep.est_in, sec=round(time.time() - t0, 1), status="failed", task="chat", error=str(ex)[:160])
                    raise
                finally:
                    SE.save_session(sess)
        steps: list = []                                         # what the program did with the answer, written to the trace (specialist_trace.jsonl) so a missing proposal can be explained later
        raw = raw0 = ""
        shown_reply, offered, err = "", False, ""
        try:
            raw = raw0 = await loop.run_in_executor(None, work)
            song_sess = sessions.get(sid)
            corrections = dict(song_sess.an_corr) if song_sess is not None else {}
            out = SE.handle_chat_answer(spec, raw, text, selected, has_sel, instruction, sel_bounds=(a, b) if has_sel else None, corrections=corrections, song_id=sid, sheet_id=sheet0)
            if meta.get("tool_steps"):
                out.steps = list(meta["tool_steps"]) + list(out.steps)
            if spec == "songwriter" and SE.open_task_waits(instruction, out.reply, bool(out.proposal or out.edits)):
                _open_task[task_key] = True
            steps = out.steps
            offered, shown_reply = out.offered, out.reply
            append_chat(sid, M.ChatMessage("assistant", out.reply, proposal=out.proposal, scope="selection" if has_sel else "all", snap_text=selected if has_sel else text,
                                           snap_start=a if has_sel else 0, snap_end=b if has_sel else len(text), sheet_id=sheet0, specialist=spec, edits=out.edits,
                                           notes=list(out.notes) + ([meta['session_note']] if meta.get('session_note') else []), hints=out.hints, rejected=out.rejected, caption=out.caption, settings=dict(out.settings), settings_info=dict(out.settings_info)))
            key_w = (sid, sheet0, spec, meta.get("generation"))
            if backend != "grok" and meta.get("usage", 0) >= SE.WARN_RATIO and key_w not in ctx_warned:
                ctx_warned.add(key_w)
                append_chat(sid, M.ChatMessage("note", f"Heads-up: this conversation is using about {int(meta['usage'] * 100)}% of the local model's memory. Start a new session soon with the New session button (it clears this conversation), or switch to Grok.",
                                               sheet_id=sheet0, specialist=spec))
        except SE.ContextFull as ex:                                 # the local model's context is full: nothing was sent, nothing was folded away
            append_chat(sid, M.ChatMessage("note", f"{ex} Press the New session button to start again (it clears this conversation), or switch to Grok.", sheet_id=sheet0, specialist=spec))
        except LC.LLMFormatError as ex:
            text_only = SE.clean_braces(re.sub(r"(?m)^\s*=+\s*[A-Z ]*=*\s*$", "", raw or "").strip())          # the marker lines are removed; what is left is only READ, never applied
            if text_only:
                append_chat(sid, M.ChatMessage("assistant", text_only, sheet_id=sheet0, specialist=spec,
                                               notes=[f"The answer did not follow the reply format ({ex}), so it is shown as text only. Nothing can be applied from it and nothing was changed."]))
            else:
                append_chat(sid, M.ChatMessage("note", f"The AI answer did not follow the format ({ex}). Nothing was changed. Try again.", sheet_id=sheet0, specialist=spec))
        except Exception as ex:
            err = str(ex)
            append_chat(sid, M.ChatMessage("note", f"AI error: {ex}", sheet_id=sheet0, specialist=spec))
        finally:
            had_chat_focus = _chat_focus["on"] or (time.time() - _chat_focus.get("blur_t", 0.0) < 1.0)       # the box had the focus when the answer arrived, or let go of it just now (the redraw of the list can do that)
            TRC.log_event("chat", specialist=spec, song_id=sid, sheet_id=sheet0, backend=backend, model=model, instruction=instruction, lyrics_hash=LA.text_hash(text), lyrics=text, selected=selected,
                          request_tail=user_msg[-1800:], raw_answer=raw0, steps=steps, reply_shown=shown_reply, proposal_offered=offered, error=err, **meta)
            S.chat_busy_song = ""
            chat_who.value = ""
            chat_busy.visible = False; safe_update(chat_busy, chat_who)
            page.run_task(refocus_chat, had_chat_focus)

    async def send_chat(e=None):
        txt = (chat_tf.value or "").strip()
        if not txt or not S.song:
            return
        if archive_note():
            return
        chat_tf.value = ""; safe_update(chat_tf)
        append_chat(S.song.id, M.ChatMessage("user", txt, specialist=S.spec, sheet_id=S.song.book.active_id))
        await ask_ai(txt)

    # ═══ AI Actions (Workspace): Continue / Increase Emotion / Improve Singability / Translate. Same transport, parser, chat proposal and Apply as the chat. ═══════════════════════════
    def action_counts(sess):
        """{file line: EFFECTIVE syllable count} of the active song when its syllable result is current (Phase 1-2, manual corrections included); None otherwise. Never recounted."""
        info = sess.analysis if sess else None
        if not (info and "result" in info and info["snap"].text_hash == LA.text_hash(lyr())):
            return None
        effs, _ = LA.effective_lines(info["result"], sess.an_corr)
        return {e.line.file_line: e.count for e in effs}

    fmt_ui = {"btn": None, "ring": None, "token": 0, "done": set()}

    def refresh_actions_ui():
        busy = bool(S.chat_busy_song) or bool(S.an_busy) or rep_busy()
        ai_actions_btn.disabled = busy
        btn = fmt_ui["btn"]
        if btn is not None:
            btn.disabled = busy
            safe_update(ai_actions_btn, btn)
        else:
            safe_update(ai_actions_btn)

    async def run_action(req: "AC.ActionRequest", song_id: str, regen: bool = False):
        """Run one action on the target CAPTURED in `req` (never on the caret as it is now). The answer becomes a chat proposal; nothing in the lyrics changes."""
        if not S.song or S.song.id != song_id:
            toast("The song changed. Nothing was sent.", C["amber"]); return
        if S.chat_busy_song or S.an_busy:
            toast("The AI is still busy. Try again in a moment.", C["amber"]); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        song, text, sess = S.song, lyr(), sessions.get(song_id)
        sheet0 = song.book.active_id
        if not AC.request_valid(req, text):
            toast("The lyrics changed since you chose the target. Nothing was sent: choose the target again.", C["amber"]); return
        req.lyrics_hash = AC.text_hash(text)                    # the answer will be checked against exactly this text
        try:
            req.model, _p = an_identity(S.backend)
        except Exception:
            req.model = ""
        req.backend = S.backend
        label = AC.describe(req, text)
        system, user_msg = AC.build_messages(req, song.title, {"genre": song.settings.genre, "mood": song.settings.mood, "bpm": song.settings.bpm}, text,
                                             action_counts(sess) if req.action in ("singability", "phrasing") else None)
        routed = SPEC_ACTION.get(req.action, "") if req.action != "custom" else ""          # Saved / custom actions keep their old behaviour (no specialist)
        if routed:
            set_spec(routed)
        tag = {"specialist": routed or "songwriter", "sheet_id": sheet0}          # a custom action is shown in the Songwriter AI's chat too (it has no id of its own, but it must not fall into the archive)
        if not regen:
            append_chat(song_id, M.ChatMessage("user", label, **tag))
        pick_ai_tab("chat")
        S.chat_busy_song = song_id
        chat_busy.visible = True; safe_update(chat_busy)
        refresh_actions_ui()
        loop = asyncio.get_running_loop()
        backend, host = S.backend, S.host
        proposal = ""
        try:
            prop, user_now = "", user_msg
            for attempt in (0, 1):                                                          # a model that gives the old lyrics back is asked ONCE more to really rewrite them
                if routed:
                    raw = await loop.run_in_executor(None, spec_ask(routed, None, "action:" + req.action), system, user_now)
                elif backend == "grok":
                    g = LC.grok_creds()
                    raw = await loop.run_in_executor(None, LC.ask_grok, g["key"], g["model"], system, user_now)
                else:
                    raw = await loop.run_in_executor(None, LC.ask_local, host, LLM_DIR / backend.split(":", 1)[1], system, user_now)
                reply, proposal = LC.parse_answer(raw)
                log.debug("ai action %s raw (attempt %s): %r", req.action, attempt + 1, raw)
                if not proposal.strip():
                    break
                try:
                    prop = AC.check_proposal(req, proposal, text)
                    break
                except AC.ActionError as ex:
                    if str(ex) in AC.RETRY_NOTES and attempt == 0:
                        user_now = user_msg + chr(10) * 2 + AC.RETRY_NOTES[str(ex)]
                        continue
                    raise
            if not proposal.strip():
                append_chat(song_id, M.ChatMessage("assistant", reply, **tag))                     # the AI declined: its reply is shown, there is nothing to apply
            else:
                scope = "insert" if req.mode == "insert" else ("all" if req.mode == "all" else "selection")
                d = req.to_dict(); d["label"] = label
                append_chat(song_id, M.ChatMessage("assistant", reply, proposal=prop, scope=scope, snap_text=req.target, snap_start=req.start, snap_end=req.end,
                                                   action=req.action, action_req=d, snap_hash=req.lyrics_hash, sheet_id=sheet0, specialist=routed or "songwriter"))
        except LC.LLMFormatError as ex:
            log.debug("ai action format: %s", ex)
            append_chat(song_id, M.ChatMessage("note", I18N.tr("the AI's answer could not be read. Try again."), **tag))
        except AC.ActionError as ex:
            shown = proposal.strip()                            # a refused proposal is still shown (it can be read and copied), but it is never offered for Apply
            append_chat(song_id, M.ChatMessage("note", str(ex) + (f"\n\nWhat the AI returned (not applied, you can copy it):\n{shown[:1800]}{'…' if len(shown) > 1800 else ''}" if shown else ""), **tag))
        except Exception as ex:
            append_chat(song_id, M.ChatMessage("note", f"AI error: {ex}", **tag))
        finally:
            S.chat_busy_song = ""
            chat_busy.visible = False; safe_update(chat_busy)
            refresh_actions_ui()
        if S.song and S.song.id == song_id:
            pick_ai_tab("chat")

    def start_action(action: str, opts: dict | None = None):
        """A Workspace button: capture the target NOW (the selection / caret the editor reported, S.sel), show the target when it was not selected, then send."""
        if not S.song:
            toast("Open or create a song first.", C["amber"]); return
        if S.chat_busy_song or S.an_busy:
            toast("The AI is still busy. Try again in a moment.", C["amber"]); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        song_id, text = S.song.id, lyr()
        try:
            req = AC.make_request(action, text, S.sel, opts, S.backend)
        except AC.ActionError as ex:
            toast(str(ex), C["amber"]); return
        if req.mode == "line" and action in ("emotion", "singability"):
            def go(e=None):
                page.pop_dialog()
                page.run_task(run_action, req, song_id)
            page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Text(AC.AI_ACTIONS[action]["label"]), content=ft.Container(width=420, content=ft.Column([
                ft.Text("Nothing is selected, so the action will work on the line your cursor is in:", size=12, color=C["text2"]),
                ft.Container(ft.Text(req.target, size=14, color=C["text1"], selectable=True), bgcolor=C["field"], border_radius=8, padding=10, border=ft.Border.all(1, C["border"])),
                ft.Text("Only this line can change, and only after you press Apply. Cancel and select text if you want another target.", size=11, color=C["text3"])], spacing=8, tight=True)),
                actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.FilledButton("Send", on_click=go, style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))]))
            return
        page.run_task(run_action, req, song_id)

    def open_translate_dialog():
        if not S.song:
            toast("Open or create a song first.", C["amber"]); return
        if S.chat_busy_song or S.an_busy:
            toast("The AI is still busy. Try again in a moment.", C["amber"]); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        song_id, text, sel0 = S.song.id, lyr(), S.sel          # the selection is captured now: clicking inside the dialog never changes it
        a, b = sel0
        has_sel = 0 <= a < b <= len(text) and AC.has_lyric(text[a:b])
        lang_dd = dd("Target Language", [(c, n) for c, n in LANGS], "en", lambda e: None, width=300)
        style_rg = ft.RadioGroup(content=ft.Column([ft.Radio(value="faithful", label="Faithful Translation"), ft.Radio(value="singable", label="Singable Adaptation")], spacing=0), value="faithful")
        scope_rg = ft.RadioGroup(content=ft.Column([ft.Radio(value="selection", label="Selected Lyrics" + ("" if has_sel else " (nothing selected)"), disabled=not has_sel),
                                                    ft.Radio(value="line", label="Current Line"), ft.Radio(value="all", label="Entire Song")], spacing=0),
                                 value="selection" if has_sel else "line")
        hint = ft.Text("Section tags such as [Verse] and [Chorus - powerful] stay as they are. The result appears in the Chat; nothing changes until you press Apply.", size=10, color=C["text3"])

        def go(e):
            if not (S.song and S.song.id == song_id and lyr() == text):
                page.pop_dialog(); toast("The lyrics changed while the dialog was open. Nothing was sent.", C["amber"]); return
            code = lang_dd.value or "en"
            opts = {"lang": code, "lang_name": dict(LANGS).get(code, code), "style": style_rg.value or "faithful", "scope": scope_rg.value}
            try:
                req = AC.make_request("translate", text, sel0, opts, S.backend)
            except AC.ActionError as ex:
                toast(str(ex), C["amber"]); return
            page.pop_dialog()
            page.run_task(run_action, req, song_id)
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Text("Translate Lyrics"), content=ft.Container(width=400, content=ft.Column([
            lang_dd, ft.Text("Translation Mode", size=11, color=C["text3"]), style_rg, ft.Text("Scope", size=11, color=C["text3"]), scope_rg, hint], spacing=6, tight=True, scroll=ft.ScrollMode.AUTO)),
            actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.FilledButton("Translate", on_click=go, style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))]))

    def open_phrasing_dialog():
        """Smart Phrasing: the user says WHAT the AI may change (spaces, line breaks or pause marks) and on WHAT part. The answer is a chat proposal: preview, Apply, Undo."""
        if not S.song:
            toast("Open or create a song first.", C["amber"]); return
        if S.chat_busy_song or S.an_busy:
            toast("The AI is still busy. Try again in a moment.", C["amber"]); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        song_id, text, sel0 = S.song.id, lyr(), S.sel          # the selection is captured now: clicking inside the dialog never changes it
        a, b = sel0
        has_sel = 0 <= a < b <= len(text) and AC.has_lyric(text[a:b])
        def cards(options: list, state: dict, disabled: tuple = ()):
            """A choice as tidy cards (icon, text, a ring that fills for the chosen one) instead of a plain radio list. `state["v"]` is the value."""
            col = ft.Column([], spacing=6)

            def draw():
                col.controls = [ft.Container(ft.Row([ft.Icon(ic, size=18, color=C["accent"] if state["v"] == v else C["text3"]),
                                                     ft.Text(label, size=12, weight=ft.FontWeight.W_600 if state["v"] == v else ft.FontWeight.W_400, color=C["text1"] if v not in disabled else C["text3"], expand=True),
                                                     ft.Icon(ft.Icons.RADIO_BUTTON_CHECKED_ROUNDED if state["v"] == v else ft.Icons.RADIO_BUTTON_UNCHECKED_ROUNDED, size=18, color=C["accent"] if state["v"] == v else C["text3"])],
                                                    spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                                      bgcolor=ft.Colors.with_opacity(0.14, C["accent"]) if state["v"] == v else C["panel2"], border_radius=10, padding=po(12, 9, 12, 9), ink=v not in disabled,
                                      border=ft.Border.all(1, C["accent"] if state["v"] == v else C["border"]), on_click=None if v in disabled else (lambda e, v=v: pick(v)))
                                for v, ic, label in options]

            def pick(v):
                state["v"] = v
                draw()
                safe_update(col)
            draw()
            return col
        kind_state, scope_state = {"v": "all"}, {"v": "selection" if has_sel else "line"}
        kind_rg = cards([("all", ft.Icons.AUTO_FIX_HIGH_ROUNDED, "All together: spaces, line breaks and pauses (recommended)"), ("spacing", ft.Icons.SPACE_BAR_ROUNDED, "Spacing (spaces between phrases)"),
                         ("lines", ft.Icons.WRAP_TEXT_ROUNDED, "Line Breaks (split or join lines)"), ("pauses", ft.Icons.PAUSE_CIRCLE_OUTLINE_ROUNDED, "Pauses (... where a singer rests)")], kind_state)
        scope_rg = cards([("selection", ft.Icons.HIGHLIGHT_ALT_ROUNDED, "Selected Lyrics" + ("" if has_sel else " (nothing selected)")), ("line", ft.Icons.LINEAR_SCALE_ROUNDED, "Current Line"),
                          ("all", ft.Icons.LIBRARY_MUSIC_ROUNDED, "Entire Song")], scope_state, disabled=() if has_sel else ("selection",))
        hint = ft.Text("Your words are never changed, only what you choose above. Section tags stay as they are; a [Hold] cue may be removed, never added. The result appears in the Chat; nothing changes until you press Apply (Undo restores it).",
                       size=10, color=C["text3"])

        def go(e):
            if not (S.song and S.song.id == song_id and lyr() == text):
                page.pop_dialog(); toast("The lyrics changed while the dialog was open. Nothing was sent.", C["amber"]); return
            try:
                req = AC.make_request("phrasing", text, sel0, {"kind": kind_state["v"] or "all", "scope": scope_state["v"]}, S.backend)
            except AC.ActionError as ex:
                toast(str(ex), C["amber"]); return
            page.pop_dialog()
            page.run_task(run_action, req, song_id)
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Row([ft.Icon(ft.Icons.AUTO_FIX_HIGH_ROUNDED, size=20, color=C["accent"]), ft.Text("Smart Phrasing", size=17, weight=ft.FontWeight.BOLD)], spacing=8),
                                        content=ft.Container(width=440, content=ft.Column([
            ft.Text("What may the AI change?", size=11, weight=ft.FontWeight.W_600, color=C["text2"]), kind_rg,
            ft.Text("Scope", size=11, weight=ft.FontWeight.W_600, color=C["text2"]), scope_rg,
            ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, size=14, color=C["text3"]), ft.Container(hint, expand=True)], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START)], spacing=8, tight=True)),
            actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.FilledButton("Suggest", icon=ft.Icons.AUTO_AWESOME_ROUNDED, on_click=go, style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))],
            actions_padding=po(16, 4, 16, 14)))

    def sheet_label(sheet_id: str) -> str:
        sh = SH.sheet_of(S.song.book, sheet_id) if S.song else None
        return sh.name if sh else "(deleted sheet)"

    def find_msg(mid: str):
        return next((x for x in S.chat if x.id == mid), None)

    async def regenerate(mid: str, song_id: str = ""):
        """Regenerate = ask again for the SAME request. The message is found by its id (not its position) and only for the song it was shown in."""
        if not S.song or (song_id and S.song.id != song_id):
            toast("The song changed. Nothing was sent.", C["amber"]); return
        sid, m = S.song.id, find_msg(mid)
        if m is None:
            toast("That chat message no longer exists. Nothing was sent.", C["amber"]); return
        if m.sheet_id and m.sheet_id != S.song.book.active_id:
            toast(f"This result was made for the sheet “{sheet_label(m.sheet_id)}”. Open that sheet to regenerate it. Nothing was sent.", C["amber"]); return
        if m.action:                                            # an AI Action result: run the SAME action on the SAME target, never guess a new one
            req = AC.request_from_dict(m.action_req)
            if req is None:
                toast("This result has no saved request, so it cannot be repeated. Use the action again.", C["amber"]); return
            if not AC.context_unchanged(req, lyr()):            # not "is the target still at the same offsets" but "are these the lyrics the request was made for"
                toast("This request is out of date: the lyrics are not the ones it was made for (they were edited, or the proposal was applied). Nothing was sent. "
                      "Select the target and use the action again.", C["amber"]); return
            await run_action(req, sid, regen=True)
            return
        users = [x for x in S.chat[:S.chat.index(m)] if x.role == "user"]
        if users:
            await ask_ai(users[-1].text)

    def apply_proposal(mid: str, song_id: str = ""):
        """Apply one proposal. What the user pressed on is captured NOW (song, message id, proposal, lyrics, cursor) and checked again right before anything is written,
        so a callback that runs later (a dialog choice) can never write into another song, an edited proposal, a message already applied, or lyrics that changed since."""
        if not S.song or (song_id and S.song.id != song_id):
            toast("The song changed. Nothing was applied.", C["amber"]); return
        m = find_msg(mid)
        if m is None:
            toast("That chat message no longer exists. Nothing was applied.", C["amber"]); return
        if m.applied:
            return
        if m.sheet_id and m.sheet_id != S.song.book.active_id:
            toast(f"This proposal was made for the sheet “{sheet_label(m.sheet_id)}”. Open that sheet to apply it. Nothing was changed.", C["amber"]); return
        cap_song, cap_sheet, cap_fp = S.song.id, S.song.book.active_id, (m.proposal, m.scope, m.snap_text, m.snap_start, m.snap_end, m.snap_hash)
        text = lyr()
        text_h, cur = AC.text_hash(text), tuple(S.sel)
        prop = m.proposal

        def done(new_text, sel, uses_cursor=False):
            if not S.song or S.song.id != cap_song:
                toast("The song changed. Nothing was applied.", C["amber"]); return
            if S.song.book.active_id != cap_sheet:
                toast("The sheet changed. Nothing was applied: open the sheet this proposal was made for.", C["amber"]); return
            now = find_msg(mid)
            if now is None:
                toast("That chat message no longer exists. Nothing was applied.", C["amber"]); return
            if now.applied:
                toast("This proposal was already applied.", C["amber"]); return
            if (now.proposal, now.scope, now.snap_text, now.snap_start, now.snap_end, now.snap_hash) != cap_fp:
                toast("The proposal changed. Nothing was applied: press Apply again.", C["amber"]); return
            if AC.text_hash(lyr()) != text_h:
                toast("The lyrics changed again. Nothing was applied: press Apply again to see the choices for the current lyrics.", C["amber"]); return
            if uses_cursor and tuple(S.sel) != cur:
                toast("The cursor moved. Nothing was applied: press Apply again.", C["amber"]); return
            set_lyrics(new_text, sel)
            now.applied = True
            ST.save_chat(cap_song, S.chat)
            rebuild_chat()
            toast("Applied (Undo restores the previous lyrics)", C["green"])

        plan = AC.plan_apply(m.scope, text, prop, m.snap_text, m.snap_start, m.snap_end, m.snap_hash, cur[1])
        if "direct" in plan:
            done(*plan["direct"]); return
        opts = [(lab, (lambda n=n, s=s, u=u: done(n, s, u))) for lab, n, s, u in plan["options"]]
        opts.append(("Copy proposal", lambda: page.run_task(copy_text, prop, "Proposal")))

        def mk(fn):
            def h(e):
                page.pop_dialog(); fn()
            return h
        page.show_dialog(ft.AlertDialog(modal=True, title=ft.Text("The text has changed"), content=ft.Text(plan["msg"] + " Nothing was overwritten."), bgcolor=C["panel"],
                                        actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog())] + [ft.OutlinedButton(l, on_click=mk(f)) for l, f in opts]))

    def new_session(e=None):
        """The model forgets this specialist's conversation on this sheet and starts clean, and the old messages leave the chat window (this specialist's, on this sheet; the other specialists keep theirs). The reports are kept."""
        if not S.song:
            return
        if archive_note():
            return
        if S.chat_busy_song:
            toast("The AI is still answering", C["amber"]); return
        who, sheet0 = S.spec, S.song.book.active_id
        try:
            so = get_spec_session(S.song.id, sheet0, who)
            SE.new_session(so)
            SE.save_session(so)
        except Exception as ex:
            toast(f"Could not start a new session: {ex}", C["red"]); return
        S.chat = [x for x in S.chat if not (SP.owner(x.specialist) == who and not (x.specialist and x.sheet_id not in ("", sheet0)))]       # this specialist's earlier messages on this sheet disappear from the window and the chat file (also the hidden older ones)
        ok = ST.save_chat(S.song.id, S.chat) if S.chat else ST.clear_chat_file(S.song.id)
        if not ok:
            toast("The chat file could not be cleared", C["red"])
        try:
            SE.drop_live(S.song.id, sheet0, who)                                  # the next message opens a new live conversation
        except Exception:
            pass
        rebuild_chat()
        toast(f"New session with {SP.name_of(who)}: the earlier messages were cleared", C["green"])

    def clear_chat(e=None):
        if not S.song or not S.chat:
            return
        if S.spec in SP.ARCHIVE_IDS:
            toast("The saved chats of the former specialists are kept: they cannot be cleared here.", C["amber"]); return

        who, sheet0 = S.spec, S.song.book.active_id

        def do():
            S.chat = [x for x in S.chat if not spec_visible(x)]                 # only THIS specialist's chat on THIS sheet; the other specialists keep theirs
            ok = ST.save_chat(S.song.id, S.chat) if S.chat else ST.clear_chat_file(S.song.id)
            if not ok:
                toast("The chat file could not be cleared", C["red"])
            try:                                                                  # its session forgets the conversation (its reports are kept)
                so = get_spec_session(S.song.id, sheet0, who)
                so.summary, so.gen_start = "", SE.now()
                SE.save_session(so)
                SE.drop_live(S.song.id, sheet0, who)                              # the next message opens a new live conversation
            except Exception:
                pass
            rebuild_chat()
        confirm("Clear chat", f"Delete your chat with {SP.name_of(who)} on this sheet? The chats with the other specialists are kept.", "Clear", do, danger=True)

    # ═══ PLAYER bars ════════════════════════════════════════════════════════════════════════════════════════
    def make_player_bar(full: bool):
        cover = ft.Container(width=44, height=44)
        title = ft.Text("Nothing playing", size=13, weight=ft.FontWeight.W_600, color=C["text1"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        sub = ft.Text("", size=11, color=C["text3"], max_lines=1)
        now_lbl = ft.Text("", size=10, weight=ft.FontWeight.BOLD, color=C["accent"])
        time_txt = ft.Text("0:00 / 0:00", size=11, color=C["text2"])
        seek = ft.Slider(min=0, max=1000, value=0, height=18, active_color=C["accent"], on_change_end=lambda e: player.seek_frac(e.control.value / 1000.0))
        play = ft.IconButton(ft.Icons.PLAY_ARROW_ROUNDED, icon_size=26 if full else 22, width=40 if full else 32, height=40 if full else 32, padding=0, icon_color="#FFFFFF", bgcolor=C["accent"],
                             on_click=lambda e: player.toggle())
        prev = ibtn(ft.Icons.SKIP_PREVIOUS_ROUNDED, "Previous", lambda e: player.prev(), 20, w=30)
        nxt = ibtn(ft.Icons.SKIP_NEXT_ROUNDED, "Next", lambda e: player.next(), 20, w=30)
        stop = ibtn(ft.Icons.STOP_ROUNDED, "Stop", lambda e: player.stop(), 20, w=30)
        vol = ft.Slider(min=0, max=100, value=player.volume * 100, width=110, on_change=lambda e: player.set_volume(e.control.value / 100.0), height=18, active_color=C["text2"])
        state = {"cid": None}

        def refresh(positions: bool = True):
            cur = player.current
            if cur is None:
                title.value, sub.value = "Nothing playing", ""
                cover.content = None
                state["cid"] = None
            else:
                title.value, sub.value = cur.title, cur.label
                sig = (cur.song_id, VX.song_cover(cur.song_id))                      # the icon changes when the song's picture does
                if state["cid"] != sig:
                    cover.content = cover_box(cur.song_id, 44)
                    state["cid"] = sig
            play.icon = ft.Icons.PAUSE_ROUNDED if player.state == "playing" else ft.Icons.PLAY_ARROW_ROUNDED
            now_lbl.value = "Now Playing" if cur is not None else ""
            safe_update(now_lbl)
            if positions:
                ln, ps = player.length_ms(), player.position_ms()
                time_txt.value = f"{fmt_dur(ps / 1000)} / {fmt_dur(ln / 1000)}"
                seek.value = (ps / ln * 1000.0) if ln else 0
            safe_update(cover, title, sub, play, time_txt, seek)
        player_views.append(refresh)
        info = ft.Row([cover, ft.Column([now_lbl, title, sub] if full else [title, sub], spacing=0, expand=True)], spacing=10, expand=True)
        if full:
            ctl = ft.Container(bgcolor=C["panel"], border=ft.Border(top=ft.BorderSide(1, C["border"])), padding=po(16, 8, 16, 8),
                               content=ft.Row([ft.Container(info, expand=2), prev, play, stop, nxt, time_txt, ft.Container(seek, expand=5), ft.Icon(ft.Icons.VOLUME_UP_ROUNDED, size=18, color=C["text2"]), vol],
                                              spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        else:
            ctl = ft.Container(bgcolor=C["panel2"], border_radius=10, padding=po(10, 8, 10, 4),
                               content=ft.Column([ft.Row([cover, ft.Column([title, ft.Row([sub, ft.Container(expand=True), time_txt], spacing=4)], spacing=0, expand=True)], spacing=10),
                                                  ft.Row([prev, play, nxt, ft.Container(seek, expand=True)], spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER)], spacing=2))
        refresh()
        return ctl

    def refresh_play_icons():
        for lst in (reg_output, reg_libtakes):
            for tid, b in lst:
                b.icon = ft.Icons.PAUSE_ROUNDED if player.is_playing_take(tid) else ft.Icons.PLAY_ARROW_ROUNDED
                safe_update(b)
        for sid, tid, b in reg_libsongs:
            b.icon = ft.Icons.PAUSE_ROUNDED if player.is_playing_take(tid) else ft.Icons.PLAY_ARROW_ROUNDED
            safe_update(b)

    def on_player_state():
        for v in list(player_views):
            v(True)
        if S.tab == "library":
            rebuild_tracks(); rebuild_queue_panel()              # the Song List shows which take is playing; the global player is not touched
        else:
            rebuild_output()
        refresh_play_icons()
    player.subscribe(on_player_state)

    _repaint_play = {"until": None}                       # (song_id, take_id, stop_ms) while "play selection" is armed; cleared when that take is no longer current

    async def player_tick():
        while True:
            await asyncio.sleep(0.4)
            try:
                player.tick()
                lim = _repaint_play.get("until")
                if lim and player.state == "playing":
                    sid, tid, stop_ms = lim
                    cur = player.current
                    if not (cur and cur.song_id == sid and cur.take_id == tid):
                        _repaint_play["until"] = None
                    elif player.position_ms() >= int(stop_ms):
                        _repaint_play["until"] = None
                        player.toggle()
                if player.state == "playing":
                    for v in list(player_views):
                        v(True)
                    if _tl_hook["tick"] is not None:
                        _tl_hook["tick"]()
            except Exception as ex:
                dbg_once("player tick", ex)

    global_player = make_player_bar(True)                  # the one player bar of the program (never rebuilt when the tab changes)

    # ═══ MY MUSIC page ══════════════════════════════════════════════════════════════════════════════════════
    projects_list = ft.ListView(expand=True, spacing=6, padding=po(0, 0, 6, 0))
    tracks_list = ft.ListView(expand=True, spacing=4, padding=po(0, 0, 6, 0))
    tracks_title = ft.Text("", size=12, color=C["text2"])
    queue_list = ft.ListView(expand=True, spacing=2)
    queue_src_txt = ft.Text("", size=11, color=C["text3"])
    lib_count = ft.Text("", size=12, color=C["text3"])
    cards_row = ft.Row(spacing=12)
    sort_dd = ft.Dropdown(value=S.sort, width=180, options=[ft.dropdown.Option("new", "Recently Updated"), ft.dropdown.Option("created", "Date Created"), ft.dropdown.Option("title", "Title A-Z"),
                                                             ft.dropdown.Option("takes", "Most Takes")], on_select=synced(lambda e: (setattr(S, "sort", e.control.value), rebuild_lib_page())), **field_style(height=38))
    lib_search = ft.TextField(hint_text="Search songs...", prefix_icon=ft.Icons.SEARCH_ROUNDED, on_change=lambda e: on_search_change(e),
                              **field_style(width=260, height=38, hint_style=ft.TextStyle(color=C["text3"])))
    _search_job = {"n": 0}

    def on_search_change(e):
        """Typing in a search field: the text is kept at once; the song lists are rebuilt ONCE when the typing pauses for 250 ms (a rebuild per key made typing lag). Both fields show the same search."""
        S.search = e.control.value or ""
        other = lib_search if e.control is search_tf else search_tf
        if (other.value or "") != S.search:
            other.value = S.search
            safe_update(other)
        _search_job["n"] += 1
        n = _search_job["n"]

        async def later():
            await asyncio.sleep(0.25)
            if _search_job["n"] == n:                            # no newer key since
                rebuild_song_list(); rebuild_lib_page()
        page.run_task(later)

    def source_tracks() -> list:
        """The Song List: [(song, take)] of the selected source, in display order. Takes only come from the library, which lists takes whose audio file exists."""
        src = S.source
        if src["type"] == "project":
            x = lib_entry(src["id"])
            if x:
                return [(x["song"], t) for t in sorted(x["takes"], key=lambda t: t.number, reverse=True)]
            S.source = {"type": "all", "id": None}              # the project is gone
        out = [(x["song"], t) for x in S.lib for t in x["takes"] if S.source["type"] == "all" or t.rating > 0]
        out.sort(key=lambda p: (p[1].created, p[1].number), reverse=True)
        return out

    def source_label() -> str:
        src = S.source
        if src["type"] == "project":
            x = lib_entry(src["id"])
            return x["song"].title if x else "All Songs"
        return COLLECTIONS[src["type"]][0]

    def select_source(kind: str, song_id: str | None = None):
        S.tracks_all = False
        S.source = {"type": kind, "id": song_id}                # only the Song List changes; the queue that is playing is not touched
        rebuild_lib_page()

    def collection_card(key: str, count: int):
        title, icon = COLLECTIONS[key]
        on = S.source["type"] == key
        return ft.Container(expand=True, padding=po(18, 14, 18, 14), border_radius=12, ink=True, on_click=lambda e, k=key: select_source(k), bgcolor=C["accent_soft"] if on else C["panel"],
                            border=ft.Border.all(2 if on else 1, C["accent"] if on else C["border"]),
                            content=ft.Row([ft.Icon(icon, size=28, color=C["amber"] if key == "favorites" else C["accent"]),
                                            ft.Column([ft.Text(title, size=16, weight=ft.FontWeight.BOLD, color=C["text1"]), ft.Text(f"{count} track{'s' if count != 1 else ''}", size=12, color=C["text2"])], spacing=2, expand=True),
                                            ft.Icon(ft.Icons.CHECK_CIRCLE_ROUNDED, size=22, color=C["accent"], visible=on)], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER))

    def play_from_song_list(t: M.Take):
        """Play on a track of the Song List: the whole Song List (in the order shown, playable files only) becomes the queue and starts at this track."""
        items = [QItem(s.id, tk.id, s.title, f"Take {tk.number:02d}", str(p)) for s, tk in source_tracks() if (p := ST.take_path(tk))]
        idx = next((i for i, it in enumerate(items) if it.take_id == t.id), -1)
        if idx < 0:
            toast("This track has no playable audio file.", C["amber"]); return      # the queue that was playing stays as it is
        label = source_label()
        if player.is_current(t.id) and S.queue_src == label and [it.take_id for it in items] == [q.take_id for q in player.queue]:
            player.toggle(); return                              # the same track of the same queue: ordinary play / pause
        S.queue_src = label
        player.play_queue(items, idx)
        if player.error:
            toast(f"Cannot play: {player.error}", C["red"])

    def project_card(x):
        s: M.Song = x["song"]
        on = S.source["type"] == "project" and S.source["id"] == s.id
        n = len(x["takes"])
        menu = ft.PopupMenuButton(icon=ft.Icons.MORE_VERT_ROUNDED, icon_size=18, icon_color=C["text2"], tooltip="More Options", items=[
            ft.PopupMenuItem(content=ft.Text("Edit Song"), on_click=lambda e, i=s.id: open_song(i)),
            ft.PopupMenuItem(content=ft.Text("Rename"), on_click=lambda e, i=s.id: rename_dialog(i)),
            ft.PopupMenuItem(content=ft.Text("Save a copy (Save As)"), on_click=lambda e, i=s.id: save_as_dialog(i)),
            ft.PopupMenuItem(content=ft.Text("Delete"), on_click=lambda e, i=s.id: delete_song_confirm(i))])
        return ft.Container(padding=po(10, 8, 6, 8), border_radius=10, ink=True, on_click=lambda e, i=s.id: select_source("project", i), bgcolor=C["accent_soft"] if on else C["panel"],
                            border=ft.Border.all(2 if on else 1, C["accent"] if on else C["border"]),
                            content=ft.Row([cover_box(s.id, 54, 10),
                                            ft.Column([ft.Text(s.title, size=14, weight=ft.FontWeight.W_600, color=C["text1"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                                       ft.Text(f"{s.settings.genre or '-'} · {n} Take{'s' if n != 1 else ''}", size=12, color=C["text2"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)], spacing=2, expand=True),
                                            ibtn(ft.Icons.EDIT_ROUNDED, "Edit Song", lambda e, i=s.id: open_song(i), 20), menu],
                                           spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))

    TRACK_LIMIT = 60

    def rebuild_tracks():
        reg_libtakes.clear()
        tr = source_tracks()
        tracks_title.value = f"{source_label()} · {len(tr)} track{'s' if len(tr) != 1 else ''}"
        proj = S.source["type"] == "project"
        shown = tr if S.tracks_all else tr[:TRACK_LIMIT]
        rows = [take_row(s.id, s.title, t, reg_libtakes, None, on_changed=reload_takes, show_title=not proj, play_fn=lambda t=t: play_from_song_list(t)) for s, t in shown]
        if len(tr) > len(shown):                                  # building every row of a long list is what made the page slow: the rest is one click away
            rows.append(ft.TextButton(f"Show all {len(tr)} tracks", icon=ft.Icons.EXPAND_MORE_ROUNDED, on_click=lambda e: (setattr(S, "tracks_all", True), rebuild_tracks()),
                                      style=ft.ButtonStyle(color=C["accent"])))
        if not rows:
            if proj:
                rows = [ft.Text("No tracks generated yet.", size=13, color=C["text3"])]
            else:
                rows = [ft.Column([ft.Text("No tracks available.", size=14, weight=ft.FontWeight.W_600, color=C["text2"])] +
                                  ([ft.Text("Rate a take with stars to add it to Favorites.", size=12, color=C["text3"])] if S.source["type"] == "favorites" else []), spacing=4)]
        tracks_list.controls = rows
        safe_update(tracks_title, tracks_list)

    def rebuild_queue_panel():
        rows = []
        for i, q in enumerate(player.queue):
            cur = i == player.index
            rows.append(ft.Container(padding=po(8, 5, 8, 5), border_radius=6, ink=True, bgcolor=C["accent_soft"] if cur else "#00000000",
                                     on_click=lambda e, k=i: player._start(k),
                                     content=ft.Row([ft.Icon(ft.Icons.GRAPHIC_EQ_ROUNDED if cur else ft.Icons.MUSIC_NOTE_OUTLINED, size=15, color=C["accent"] if cur else C["text3"]),
                                                     ft.Text(f"{q.title} · {q.label}", size=12, color=C["text1"] if cur else C["text2"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                                                     ft.Text("Now Playing", size=10, weight=ft.FontWeight.BOLD, color=C["accent"], visible=cur)], spacing=8)))
        queue_list.controls = rows or [ft.Text("The queue is empty. Press Play on a track.", size=12, color=C["text3"])]
        queue_src_txt.value = f"Source: {S.queue_src}" if (S.queue_src and player.queue) else ""
        safe_update(queue_list, queue_src_txt)

    _lib_sig = {"v": None}

    def lib_signature() -> tuple:
        """What the Library page shows: the songs (title, take count, favourites), the search, the sort, the source and which take is playing."""
        cur = player.current.id if getattr(player, "current", None) is not None and hasattr(player.current, "id") else ""
        return (S.search, S.sort, S.source["type"], S.source["id"], S.tracks_all, cur, getattr(player, "state", ""), len(player.queue), getattr(player, "index", 0),
                tuple((x["song"].id, x["song"].title, x["song"].settings.genre, len(x["takes"]), sum(1 for t in x["takes"] if t.rating > 0)) for x in S.lib))

    def rebuild_lib_page(only_if_changed: bool = False):
        if only_if_changed:
            try:
                sig = lib_signature()
            except Exception:
                sig = None
            if sig is not None and sig == _lib_sig["v"]:
                return                                          # nothing the page shows has changed since it was built
        _lib_sig["v"] = None
        vis = filtered_lib()
        n_all = sum(len(x["takes"]) for x in S.lib)
        n_fav = sum(1 for x in S.lib for t in x["takes"] if t.rating > 0)
        cards_row.controls = [collection_card("all", n_all), collection_card("favorites", n_fav)]
        projects_list.controls = [project_card(x) for x in vis] or [ft.Text("Nothing matches your search." if S.search.strip() else "No songs yet. Press New Song to start.", size=13, color=C["text3"])]
        lib_count.value = f"{len(vis)} song{'s' if len(vis) != 1 else ''}"
        safe_update(cards_row, projects_list, lib_count)
        rebuild_tracks()
        rebuild_queue_panel()
        try:
            _lib_sig["v"] = lib_signature()
        except Exception:
            _lib_sig["v"] = None

    # ═══ SHELL ══════════════════════════════════════════════════════════════════════════════════════════════
    conn_dot = ft.Container(width=9, height=9, border_radius=5, bgcolor=C["red"])
    conn_txt = ft.Text("ComfyUI not connected", size=12, color=C["text2"])
    work_ring = ft.ProgressRing(width=16, height=16, stroke_width=2, color=C["accent"], visible=False)         # "AI is working…" sits in the top bar, in front of the ComfyUI status
    work_txt = ft.Text("AI is working…", size=11, color=C["accent"], visible=False)
    page_holder = ft.Container(expand=True)
    tabs_row = ft.Row(spacing=4, scroll=ft.ScrollMode.AUTO, expand=True, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def set_tab(key: str):
        if key == "editor" and not S.song:
            key = "library"
        if key == "library":
            park()                                               # the editor's controls leave the page: remember this song's caret and scroll position
        S.tab = key
        page_holder.content = library_page if key == "library" else editor_page
        if time.time() - S.lib_at > 60:                          # every change of the library (save, rename, delete, a new take) refreshes it itself: switching tabs does not scan the folder again
            refresh_lib()
        if key == "library":
            rebuild_lib_page(only_if_changed=True)
        else:
            rebuild_song_list()
        render_tabs()
        safe_update(page_holder)

    def host_dialog(e=None):
        tf = ft.TextField(label="ComfyUI address", value=S.host, **field_style(height=48))
        g = LC.grok_creds()
        gk = ft.TextField(label="Grok API key (xAI)", value=g["key"], password=True, can_reveal_password=True, **field_style(height=48))
        gm = ft.TextField(label="Grok model", value=g["model"], **field_style(height=48))
        try:
            wf_default = LC.workflow_context(LLM_DIR / S.backend.split(":", 1)[1]) if S.backend and S.backend != "grok" else 0
        except Exception:
            wf_default = 0
        wf_default = wf_default or 8192
        ctx_local = ft.TextField(label="Local AI context (tokens)", value=str(cfg.get("ctx_local") or ""), hint_text=f"default {wf_default}", keyboard_type=ft.KeyboardType.NUMBER, **field_style(height=48))
        ctx_grok = ft.TextField(label="Grok context (tokens)", value=str(cfg.get("ctx_grok") or ""), hint_text=f"default {LC.GROK_CONTEXT_DEFAULT}", keyboard_type=ft.KeyboardType.NUMBER, **field_style(height=48))

        fs_dd = ft.Dropdown(label="Text size", value=str(FSC.percent()), **field_style(height=48), options=[ft.dropdown.Option(str(p), FSC.label(p)) for p in FSC.CHOICES + (() if FSC.percent() in FSC.CHOICES else (FSC.percent(),))])

        def do(ev):
            S.host = (tf.value or "").strip() or "127.0.0.1:8188"
            cfg["host"] = S.host
            new_pct = FSC.normalize(fs_dd.value)
            if new_pct == FSC.DEFAULT:
                cfg.pop("font_scale", None)                                        # 100% = not set = the sizes of the code
            else:
                cfg["font_scale"] = new_pct
            if new_pct != FSC.percent():
                FSC.apply(ft, page, new_pct)                                       # takes effect at once, on every open window
            bad = []
            for key, tf_, label in (("ctx_local", ctx_local, "Local AI context"), ("ctx_grok", ctx_grok, "Grok context")):
                txt = (tf_.value or "").replace(",", "").strip()
                if not txt:
                    cfg.pop(key, None)                                             # empty: the default of the flow is used
                elif txt.isdigit() and int(txt) >= LC.CTX_MIN:
                    cfg[key] = int(txt)
                else:
                    bad.append(label)
                    cfg.pop(key, None)
            _save_cfg(cfg)
            LC.set_context_overrides(cfg.get("ctx_local"), cfg.get("ctx_grok"))
            if bad:
                toast(f"{' and '.join(bad)}: a number of at least {LC.CTX_MIN} is needed, so the default is used.", C["amber"])
            if (gk.value or "").strip() != g["key"] or (gm.value or "").strip() != g["model"]:
                try:
                    LC.save_grok_creds(gk.value or "", gm.value or "")
                except Exception as ex:
                    toast(f"Could not save the Grok settings: {ex}", C["red"])
            page.pop_dialog(); init_backend_dd(); safe_update(backend_dd, an_backend_dd)
        def away(fn):
            """These actions open a dialog or a folder of their own, so Settings closes first."""
            return lambda ev: (page.pop_dialog(), fn())

        def tool(label, fn, icon):
            return ft.OutlinedButton(label, icon=icon, on_click=away(fn), height=36, style=ft.ButtonStyle(color=C["text1"], side=ft.BorderSide(1, C["border"]), alignment=ft.Alignment(-1, 0)))
        log_sw = ft.Switch(label="Keep the specialist log", value=bool(TRC.ENABLED), on_change=lambda ev: toggle_trace(bool(ev.control.value)))
        page.show_dialog(ft.AlertDialog(title=ft.Text("Settings"), bgcolor=C["panel"], content=ft.Container(width=420, height=dlg_h(560), content=ft.Column([
            ft.Text("Text size", size=12, weight=ft.FontWeight.BOLD, color=C["text2"]), fs_dd,
            ft.Text("Applies to the whole program after Save. Dialogs that are already open keep their size until they are opened again.", size=11, color=C["text3"]),
            ft.Divider(height=8, color=C["border"]),
            ft.Text("ComfyUI", size=12, weight=ft.FontWeight.BOLD, color=C["text2"]), tf,
            ft.Text("Grok AI", size=12, weight=ft.FontWeight.BOLD, color=C["text2"]), gk, gm,
            ft.Text("The key is stored in the credentials file shared with Story Generator and Prompt Lab. Leave it empty to turn Grok off.", size=11, color=C["text3"]),
            ft.Divider(height=8, color=C["border"]),
            ft.Text("AI context size", size=12, weight=ft.FontWeight.BOLD, color=C["text2"]), ctx_local, ctx_grok,
            ft.Text("Leave a field empty to use its default (Local: the n_ctx of the workflow file; Grok: 32768). A Local value is sent to the model as n_ctx, so the model must be able to load it. A request may use only part of it, and the rest is kept for the answer.", size=11, color=C["text3"]),
            ft.Divider(height=8, color=C["border"]),
            ft.Text("Specialist log", size=12, weight=ft.FontWeight.BOLD, color=C["text2"]), log_sw,
            ft.Text("Your lyrics and the AI answers are kept on this computer only.", size=11, color=C["text3"]),
            ft.Row([tool("Open the specialist log folder", open_trace_folder, ft.Icons.FOLDER_OPEN_OUTLINED)]),
            ft.Row([tool("Delete the specialist log", delete_trace, ft.Icons.DELETE_OUTLINE_ROUNDED)]),
            ft.Divider(height=8, color=C["border"]),
            ft.Text("Specialist prompts", size=12, weight=ft.FontWeight.BOLD, color=C["text2"]),
            ft.Row([tool("Open the prompts folder", open_prompts_folder, ft.Icons.FOLDER_OPEN_OUTLINED)]),
            ft.Row([tool("Reload the prompts", reload_prompts, ft.Icons.REFRESH_ROUNDED)]),
            ft.Row([tool("View the effective prompt", show_effective_prompt, ft.Icons.DESCRIPTION_OUTLINED)]),
            ft.Row([tool("Reset this prompt to default…", reset_prompt_confirm, ft.Icons.RESTORE_ROUNDED)])], spacing=8, scroll=ft.ScrollMode.AUTO)),
            actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.FilledButton("Save", on_click=do)]))

    def show_logs(e=None):
        """The top-bar log viewer: system logs and the recorded LLM conversations. Nothing is written."""
        state = {"log": "system", "session": "", "total": 0, "path": "", "loaded": False, "stop": False, "follow": True, "last": None}
        log_dd = ft.Dropdown(label="Log", value="system", width=250, options=[
            ft.dropdown.Option("system", "System log"), ft.dropdown.Option("errors", "Error log"), ft.dropdown.Option("calls", "AI call log"),
            ft.dropdown.Option("trace", "Specialist conversation"), ft.dropdown.Option("sessions", "Local LLM sessions")], **field_style(text_size=12))
        sess_dd = ft.Dropdown(label="Session", value=None, visible=False, expand=True, **field_style(text_size=12))
        path_txt = ft.Text(size=11, color=C["text3"], selectable=True)
        status = ft.Text(size=11, color=C["text3"])
        body = ft.Text(size=11, selectable=True, font_family="Consolas", color=C["text1"])
        wide = int(min(780, max(560, page_w() - 160)))
        box = ft.Container(width=wide, height=dlg_h(460, 0.62), bgcolor=C["field"], border=ft.Border.all(1, C["border"]), border_radius=8, padding=10,
                           content=ft.Column([body], scroll=ft.ScrollMode.AUTO, spacing=0, auto_scroll=True))        # the newest lines are at the bottom: the box follows them
        scroller = box.content

        def load_sessions():
            found, count = LV.list_sessions()
            state["loaded"], state["total"] = True, count
            sess_dd.options = [ft.dropdown.Option(p, lab) for p, lab in found]
            keys = [p for p, _lab in found]
            if state["session"] not in keys:
                state["session"] = keys[0] if keys else ""
            sess_dd.value = state["session"] or None

        def fill(live=False, tick=False) -> bool:
            """Read the selected log into the box. With tick=True (the 1-second refresh) nothing is redrawn when the text has not changed. Returns True when the text changed."""
            if state["log"] == "sessions" and not state["loaded"]:
                load_sessions()
            view = LV.read(state["log"], state["session"])
            changed = (view.path, view.text) != state["last"]
            state["last"] = (view.path, view.text)
            if tick and not changed:
                return False
            state["path"] = view.path
            notes = list(view.notes)
            if state["log"] == "sessions" and state["total"] > LV.SESSION_LIST:
                notes.append("The list shows the 30 newest session files.")
            I18N.pin_value(path_txt, view.path)
            I18N.pin_value(body, view.text)
            status.value = " · ".join(notes)
            sess_dd.visible = state["log"] == "sessions"
            if live:
                safe_update(path_txt, status, body, sess_dd, box)
            return changed

        def on_log(ev):
            state["log"] = ev.control.value or "system"
            fill(True)

        def on_session(ev):
            state["session"] = ev.control.value or ""
            fill(True)

        def refresh(ev=None):
            if state["log"] == "sessions":
                state["loaded"] = False
            fill(True)

        def open_log_folder(ev=None):
            target = Path(state["path"]) if state["path"] else LV.logs_dir()
            folder = target if target.is_dir() else target.parent
            if not folder.exists():
                folder = LV.logs_dir()
            try:
                os.startfile(str(folder))
            except Exception as ex:
                toast(f"Cannot open: {ex}", C["red"])

        def close_logs(ev=None):
            state["stop"] = True                                  # the 1-second refresh ends with the dialog
            try:
                page.pop_dialog()
            except Exception:
                pass

        def on_follow(ev):
            state["follow"] = bool(ev.control.value)
            scroller.auto_scroll = state["follow"]
            safe_update(scroller)
            if state["follow"]:
                page.run_task(to_bottom)

        async def to_bottom():
            try:
                await scroller.scroll_to(offset=-1, duration=0)
            except Exception:
                pass

        async def live_logs():
            """Like watching the log live: once a second the selected log is read again and the box stays at the newest line. Off while 'Follow live' is off, so a reader who scrolled up is not pulled down."""
            await to_bottom()
            while not state["stop"]:
                await asyncio.sleep(1.0)
                if state["stop"]:
                    break
                if state["follow"]:
                    try:
                        if fill(True, tick=True):
                            await to_bottom()
                    except Exception as ex:
                        dbg_once("live log", ex)

        follow_sw = ft.Switch(label="Follow live", value=True, scale=0.85, on_change=on_follow)
        log_dd.on_select = synced(on_log)
        sess_dd.on_select = synced(on_session)
        fill(False)
        page.show_dialog(ft.AlertDialog(title=ft.Text("View logs"), bgcolor=C["panel"], on_dismiss=lambda e: state.update(stop=True), content=ft.Column([
            ft.Row([log_dd, sess_dd, follow_sw], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER), path_txt, status, box], spacing=8, tight=True),
            actions=[ft.TextButton("Refresh", on_click=refresh), ft.TextButton("Open folder", on_click=open_log_folder), ft.TextButton("Close", on_click=close_logs)]))
        page.run_task(live_logs)

    lang_row = ft.Row(spacing=0)

    def set_ui_lang(code: str):
        """The window's language (Thai / English). Only what the windows show changes: lyrics, captions and AI prompts are never translated."""
        if code == I18N.lang():
            return
        I18N.set_lang(code)
        cfg["lang"] = code
        _save_cfg(cfg)
        build_lang_row()
        I18N.refresh(page)
        if bb["obj"] is not None and S.song:
            bb["obj"].build()                                       # chip labels follow the UI language; the caption (English phrases) does not change

    def build_lang_row():
        lang_row.controls = [ft.Container(ft.Text(t, size=11, weight=ft.FontWeight.W_600, color="#FFFFFF" if I18N.lang() == c else C["text2"]), padding=po(8, 3, 8, 3),
                                          border_radius=6, bgcolor=C["accent"] if I18N.lang() == c else "#00000000", ink=True, on_click=lambda e, c=c: set_ui_lang(c))
                             for c, t in (("th", "TH"), ("en", "EN"))]
        safe_update(lang_row)
    build_lang_row()

    top_bar = ft.Container(bgcolor=C["top"], border=ft.Border(bottom=ft.BorderSide(2, C["accent"])), padding=po(left=8, right=14, top=0, bottom=0),
                           content=ft.Row([tabs_row, work_ring, work_txt, conn_dot, conn_txt, ft.Container(lang_row, bgcolor=C["field"], border_radius=8, padding=2, tooltip="UI language"), ibtn(ft.Icons.SETTINGS_OUTLINED, "Settings (ComfyUI, Grok AI)", host_dialog, 20), ibtn(ft.Icons.ARTICLE_OUTLINED, "View logs", show_logs, 20)],
                                          spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))

    def panel(content, **kw):
        return ft.Container(content=content, bgcolor=C["panel"], border_radius=12, border=ft.Border.all(1, C["border"]), **kw)

    def divider_v(on_drag, on_end=None):
        return ft.GestureDetector(content=ft.Container(width=8, bgcolor="#00000000", content=ft.Container(width=2, bgcolor=C["border"], margin=po(3, 0, 3, 0))), drag_interval=15,
                                  on_horizontal_drag_update=on_drag, on_horizontal_drag_end=on_end, mouse_cursor=ft.MouseCursor.RESIZE_LEFT_RIGHT)

    def divider_h(on_drag, on_end=None):
        return ft.GestureDetector(content=ft.Container(height=8, bgcolor="#00000000", content=ft.Container(height=2, bgcolor=C["border"], margin=po(0, 3, 0, 3))), drag_interval=15,
                                  on_vertical_drag_update=on_drag, on_vertical_drag_end=on_end, mouse_cursor=ft.MouseCursor.RESIZE_UP_DOWN)

    # editor page columns --------------------------------------------------------------------------------------
    MIN_EDITOR_W, TOOLS_W, STRIP_W = 380, 300, 44                   # TOOLS_W: the width of the Lyrics Tools column inside the Workspace "Lyrics Tools" tab

    def seg_buttons(options, current, on_pick):
        return [ft.Container(ft.Text(t, size=12, weight=ft.FontWeight.W_600, color="#FFFFFF" if k == current else C["text2"]), padding=po(12, 6, 12, 6), border_radius=8,
                             bgcolor=C["accent"] if k == current else C["field"], ink=True, on_click=lambda e, k=k: on_pick(k)) for k, t in options]

    def _adv_changed(e):
        """The Advanced Generation Settings panel stays open / closed the way the user left it (also after a restart)."""
        try:
            v = str(e.data).lower() == "true" if e.data is not None else bool(adv_tile.expanded)
        except Exception:
            v = bool(adv_tile.expanded)
        cfg["gen_adv_open"] = v
        _save_cfg(cfg)

    def persist_layout():
        cfg.update({"ai_w": S.ai_w, "side_w": S.side_w, "side_open": S.side_open, "ws_open": S.ws_open, "ws_tab": S.ws_tab, "ws_h": S.ws_h, "tools_open": S.tools_user})
        _save_cfg(cfg)

    def coming_soon(lines: list):
        return ft.Column([ft.Row([ft.Icon(ft.Icons.HOURGLASS_EMPTY_ROUNDED, size=14, color=C["amber"]), ft.Text("Coming Soon", size=12, weight=ft.FontWeight.BOLD, color=C["amber"])], spacing=6)] +
                         [ft.Text("•  " + ln, size=12, color=C["text2"]) for ln in lines], spacing=4)

    # AI Assistant (left): the existing chat controls, moved; Analysis is only a container for later ------------------------------
    # AI Actions: the same requests the Workspace cards made (the answer arrives in this chat as a proposal); a menu beside Send.
    ai_actions_btn = ft.PopupMenuButton(icon=ft.Icons.AUTO_AWESOME_ROUNDED, icon_color=C["text2"], tooltip="AI Actions", icon_size=20, bgcolor=C["panel2"], items=[
        ft.PopupMenuItem(content=ft.Column([ft.Text("Review Lyrics", size=12, weight=ft.FontWeight.W_600, color=C["text1"]), ft.Text("Suggestions for the selection or section", size=10, color=C["text3"])], spacing=0),
                         on_click=lambda e: start_review()),
        ft.PopupMenuItem(content=ft.Column([ft.Text("Find Alternative Words", size=12, weight=ft.FontWeight.W_600, color=C["text1"]), ft.Text("For the words you selected", size=10, color=C["text3"])], spacing=0),
                         on_click=lambda e: open_alternatives()),
        *[ft.PopupMenuItem(content=ft.Column([ft.Text(AC.AI_ACTIONS[k]["label"], size=12, weight=ft.FontWeight.W_600, color=C["text1"]), ft.Text(AC.AI_ACTIONS[k]["sub"], size=10, color=C["text3"])], spacing=0),
                                              on_click=lambda e, k=k: on_action_click(k)) for k in ("continue", "emotion", "singability", "translate", "phrasing")],
        ft.PopupMenuItem(content=ft.Column([ft.Text("Song Structures", size=12, weight=ft.FontWeight.W_600, color=C["text1"]), ft.Text("Insert or arrange a structure", size=10, color=C["text3"])], spacing=0),
                         on_click=lambda e: open_structures_dialog())])
    chat_input_row = ft.Row([chat_tf, ai_actions_btn, ft.IconButton(ft.Icons.SEND_ROUNDED, icon_color="#FFFFFF", bgcolor=C["accent"], on_click=lambda e: page.run_task(send_chat), width=42, height=42, tooltip="Send")],
                            spacing=4, vertical_alignment=ft.CrossAxisAlignment.END)
    backend_dd.width = None
    backend_dd.expand = True
    chat_view = ft.Column([ft.Row([ft.Text("Model", size=11, color=C["text3"]), backend_dd], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                           ft.Row([ft.Text("Talk to", size=11, color=C["text3"]), spec_dd], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER), chat_who,
                           ft.Container(chat_list, expand=True), ft.Row([chat_new_chip], alignment=ft.MainAxisAlignment.CENTER), chat_ctx, sel_toolbar, chat_input_row], spacing=6, expand=True)
    # Lyrics Analysis (syllables: the AI splits, Python validates and counts; Phase 2: the user's manual corrections, kept apart from the raw AI result) ----
    an_cache = LA.ResultCache()
    rh_cache = LA.ResultCache()
    rt_cache = LA.ResultCache()
    th_cache = LA.ResultCache()
    an_state = {"fresh": None, "rc_all": True}
    an_ring = ft.ProgressRing(width=14, height=14, stroke_width=2, visible=False)
    an_status = ft.Text("", size=11, color=C["text3"])
    an_warn = ft.Text("", size=11, color=C["amber"], visible=False)
    an_err = ft.Text("", size=11, color=C["red"], selectable=True, visible=False)
    an_note = ft.Text("", size=11, color=C["text3"], visible=False)
    an_summary = ft.Text("", size=12, weight=ft.FontWeight.W_600, color=C["text1"])
    an_list = ft.ListView(expand=True, spacing=6, padding=po(0, 0, 4, 0))
    an_btn = ft.FilledButton("Analyze Lyrics", icon=ft.Icons.GRAPHIC_EQ_ROUNDED, height=36, on_click=lambda e: page.run_task(run_analysis, False),
                             style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF", shape=ft.RoundedRectangleBorder(radius=8)))
    an_reask = ibtn(ft.Icons.REFRESH_ROUNDED, "Ask the AI again (does not use the saved result)", lambda e: page.run_task(run_analysis, True), 18)
    an_reask.visible = False
    LANG_NAMES = dict(LANGS)

    def an_temp(kind=None):
        """Temperature for one analysis. kind None: the shared analysis_temperature, or None when it is unset, so chat and songwriting actions stay on the chat temperature.
        A real kind (syl, rhyme, rhythm, thes) never reads the chat temperature. The value is part of the saved-result key, so a different temperature never reuses another answer.
        The per-kind defaults are starting points, not a tuned optimum."""
        return LA.analysis_temperature(LC.load_cfg(), kind)

    def an_identity(backend: str, kind=None) -> tuple:
        """(model identity, request parameters) that decide whether a saved answer may be reused. A local workflow is identified by its file name AND a hash of its content
        (so another model inside the same file name is a different answer); Grok by its model name.
        kind None keeps the chat temperature in the key. A real analysis kind records its own temperature."""
        at = an_temp(kind)
        if backend == "grok":
            return LC.grok_creds()["model"], f"t={0.7 if at is None else at},max=4096,effort=low"
        wf = LLM_DIR / backend.split(":", 1)[1]
        try:
            ident = f"{wf.name}@{LA.text_hash(wf.read_text(encoding='utf-8'))[:10]}"
        except Exception:
            ident = wf.name + "@unreadable"
        c = LC.load_cfg()
        return ident, f"t={c.get('temperature', 0.7) if at is None else at},max={c.get('max_tokens', 4096)}"

    def persist_analysis(sess):
        """analysis.json of this song: the last good syllable result + the manual corrections + the last good rhyme result (atomic write; nothing that failed validation is ever
        written). The file is read first and only these keys are replaced, so saving one kind never drops the other."""
        try:
            meta = ST.load_analysis_meta(sess.song.id, sheet_key(sess))
        except Exception:
            meta = {}
        meta["version"] = 1
        meta["corrections"] = {k: c.to_dict() for k, c in sess.an_corr.items()}
        info = sess.analysis
        if info and "result" in info:
            meta["result"] = LA.analysis_to_dict(info["snap"], info["result"], info.get("at", ""))
        rh = sess.rhyme
        if rh and "result" in rh:
            meta["rhyme"] = LR.rhyme_to_dict(rh["snap"], rh["result"], rh.get("at", ""))
        rt = sess.rhythm
        if rt and "result" in rt:
            meta["rhythm"] = RT.rhythm_to_dict(rt["snap"], rt["result"], rt.get("at", ""))
        th = sess.thes
        if th and "result" in th:
            meta["thesaurus"] = TH.thes_to_dict(th["snap"], th["result"], th.get("at", ""))
        try:
            ST.save_analysis_meta(sess.song.id, meta, sheet_key(sess))
        except Exception as ex:
            toast(f"Could not save the analysis: {ex}", C["red"])

    def load_sess_analysis(sess):
        try:
            meta = ST.load_analysis_meta(sess.song.id, sheet_key(sess))
        except Exception:
            meta = {}
        for k, v in (meta.get("corrections") or {}).items():
            c = LA.correction_from_dict(v)
            if c is not None:
                sess.an_corr[str(k)] = c
        got = LA.analysis_from_dict(meta.get("result")) if meta.get("result") else None
        if got:
            d, res = got
            snap = LA.Snapshot(sess.song.id, "", 0, d["backend"], d["model"], d["params"], d["prompt_version"], known_hash=d["text_hash"])
            sess.analysis = {"snap": snap, "result": res, "at": d["at"], "loaded": True}
            an_cache.put(snap.cache_key, res)                           # the same lyrics + model + parameters need no new AI request after a restart
        rgot = LR.rhyme_from_dict(meta.get("rhyme")) if meta.get("rhyme") else None
        if rgot:
            d, res = rgot
            snap = LA.Snapshot(sess.song.id, "", 0, d["backend"], d["model"], d["params"], d["prompt_version"], known_hash=d["text_hash"], kind=LR.KIND)
            sess.rhyme = {"snap": snap, "result": res, "at": d["at"], "loaded": True}
            rh_cache.put(snap.cache_key, res)
        tgot = RT.rhythm_from_dict(meta.get("rhythm")) if meta.get("rhythm") else None
        if tgot:
            d, res = tgot
            snap = LA.Snapshot(sess.song.id, "", 0, d["backend"], d["model"], d["params"], d["prompt_version"], known_hash=d["text_hash"], kind=RT.KIND)
            sess.rhythm = {"snap": snap, "result": res, "at": d["at"], "loaded": True}
            rt_cache.put(snap.cache_key, res)
        hgot = TH.thes_from_dict(meta.get("thesaurus")) if meta.get("thesaurus") else None
        if hgot:
            d, res = hgot
            snap = LA.Snapshot(sess.song.id, "", 0, d["backend"], d["model"], d["params"], d["prompt_version"], known_hash=d["text_hash"], kind=TH.KIND)
            sess.thes = {"snap": snap, "result": res, "at": d["at"], "loaded": True}
            th_cache.put(snap.cache_key, res)
        try:
            load_spec_reports(sess)
        except NameError:
            pass                                                        # called while the UI is still being built: the reports load on the first render
        except Exception as ex:
            log.warning("saved specialist reports could not be read: %r", ex)

    def _an_info():
        sess = sessions.get(S.song.id) if S.song else None
        return sess.analysis if sess else None

    _SRC = {"ai": "≈ AI estimate", "units": "Manual segmentation", "count": "Manual count"}

    def goto_file_line(n: int, last: int | None = None):
        """Select line n (1.. in the lyrics text), or lines n..last, in the editor and move the focus there. The lyrics are not changed: only the selection is pushed, the same way a cue or undo does it."""
        if not S.song:
            return
        text = lyr()
        starts = [0] + [m.end() for m in re.finditer(r"\r\n|\r|\n", text)]
        last = n if last is None else max(n, last)
        if n < 1 or n > len(starts):
            toast(f"The lyrics have {len(starts)} lines: there is no line {n}.", C["amber"]); return
        last = min(last, len(starts))
        m = re.compile(r"\r\n|\r|\n").search(text, starts[last - 1])
        S.sel = (starts[n - 1], m.start() if m else len(text))
        if S.tab != "editor":
            set_tab("editor")
        show_lyrics_to_ui(select=S.sel, focus=True)

    def on_chat_link(e):
        """A line reference in a chat answer (แถวที่ 4): select that line in the editor. Any other link is not opened."""
        url = str(getattr(e, "data", "") or "")
        m = re.fullmatch(r"wp-line:(\d+)-(\d+)", url)
        if m:
            goto_file_line(int(m.group(1)), int(m.group(2)))

    def _an_row(e: LA.Effective, editable: bool):
        is_ja = all(sg.lang == "ja" for sg in e.segments)
        unit = "mora" if is_ja else ("syllable" if e.count == 1 else "syllables")
        if e.units is not None and e.source == "units":
            shown = " · ".join(e.units)
        else:
            shown = "   |   ".join((f"{LANG_NAMES.get(sg.lang, sg.lang.upper())}: " if len(e.segments) > 1 else "") + " · ".join(sg.units) for sg in e.segments)
        langs = " + ".join(LANG_NAMES.get(c, c.upper()) for c in dict.fromkeys(sg.lang for sg in e.segments))
        badge = ft.Container(ft.Text(str(e.count), size=14, weight=ft.FontWeight.BOLD, color="#FFFFFF"), bgcolor=C["amber"] if e.manual else C["accent"], border_radius=8,
                             padding=po(8, 2, 8, 2), tooltip=f"{e.count} {unit} · {_SRC[e.source]}")
        head = [ft.Text(str(e.line.num), size=11, color=C["text3"], width=18), ft.Text(e.line.text, size=12, color=C["text1"], expand=True, max_lines=3, overflow=ft.TextOverflow.ELLIPSIS), badge]
        if editable:
            head.append(ft.IconButton(ft.Icons.EDIT_OUTLINED, icon_size=16, width=28, height=28, padding=0, tooltip="Edit Syllables", icon_color=C["text2"], on_click=lambda ev, x=e: open_edit_syllables(x)))
        meta = f"{langs} · {e.count} {unit} · {_SRC[e.source]}" + (f" (AI said {e.ai_count})" if e.manual and e.ai_count != e.count else "") + (f" · {e.note}" if e.note else "")
        body = [ft.Row(head, spacing=4, vertical_alignment=ft.CrossAxisAlignment.START),
                ft.Text(shown, size=12, color=C["amber"] if e.source == "units" else C["accent"], selectable=True),
                ft.Text(meta, size=11, color=C["amber"] if (e.manual or e.note) else C["text3"])]
        if e.warn:
            body.append(ft.Text(e.warn, size=11, color=C["amber"]))
        row = ft.Container(ft.Column(body, spacing=2), bgcolor=C["panel2"], border_radius=8, padding=po(8, 6, 4, 6), border=ft.Border.all(1, C["amber"]) if e.manual else None)
        if editable:                                                 # the text is still the analysed one: the row can point at its line
            row.ink, row.on_click, row.tooltip = True, (lambda ev, n=e.line.file_line: goto_file_line(n)), f"Go to line {e.line.file_line} in the editor"
        return row

    def render_analysis():
        """Show the analysis of the ACTIVE song. Read-only for the lyrics: nothing here touches the lyrics field, its selection or its scroll."""
        sess = sessions.get(S.song.id) if S.song else None
        info = sess.analysis if sess else None
        busy_here = bool(S.song) and S.an_busy == S.song.id and S.an_kind == "syl"
        cur = LA.text_hash(lyr()) if S.song else ""
        has_result = bool(info and "result" in info)
        fresh = has_result and info["snap"].text_hash == cur
        version_current = (not has_result) or info["snap"].prompt_version == LA.PROMPT_VERSION
        an_state["fresh"] = fresh if has_result else None
        err = (info or {}).get("last_error")
        err_here = bool(err and err.get("text_hash") == cur)
        an_ring.visible = busy_here
        an_btn.disabled = (not S.song) or bool(S.an_busy)
        an_reask.visible, an_reask.disabled = has_result, an_btn.disabled
        an_err.value = (f"The last analysis failed: {err['message']}" + (" The earlier result below is kept." if has_result else "")) if err_here else ""
        an_err.visible = bool(an_err.value)
        an_warn.value = ("The lyrics changed after this analysis. Press Analyze Lyrics to update it. Your manual corrections are kept and apply again only to lines that still match."
                         if (has_result and not fresh) else
                         "These splits came from an older prompt. The count is a spoken-syllable estimate, not the number of sung notes. Press Analyze Lyrics to split again."
                         if (has_result and not version_current) else "")
        an_warn.visible = bool(an_warn.value)
        rows, summary, status, note = [], "", "", ""
        if busy_here:
            status = S.an_progress or "Asking the AI…"
        if has_result:
            res: LA.Analysis = info["result"]
            effs, inactive = LA.effective_lines(res, sess.an_corr)
            rows = [_an_row(e, editable=fresh and not S.an_busy) for e in effs]
            n_manual = sum(1 for e in effs if e.manual)
            summary = f"Total: {LA.effective_total(effs)} spoken units · {len(effs)} lines · not a sung-note count" + (f" · {n_manual} edited by you" if n_manual else "")
            if not busy_here:
                status = f"{info['snap'].backend.replace('local:', 'Local: ')} · {info.get('at', '')}" + (" · saved result" if (info.get("cached") or info.get("loaded")) else "") + ("" if fresh else " · outdated")
            if fresh and inactive:
                note = f"{len(inactive)} manual correction(s) belong to lines that are not in the lyrics now. They are kept and are not applied to any other line."
        elif err_here and not busy_here:
            status = "The analysis failed"
        if not rows and not busy_here and not an_err.value:
            rows = [ft.Text("Press Analyze Lyrics: the AI splits every sung line into spoken syllables (mora for Japanese) and says which language it hears. Section tags like [Verse] are skipped. "
                            "The count is made here from those units. It is not the number of notes in a melody, because no audio was heard. Use Edit to correct a line.", size=12, color=C["text3"])]
        an_list.controls = rows
        an_note.value, an_note.visible = note, bool(note)
        an_summary.value, an_status.value = summary, status
        safe_update(an_ring, an_btn, an_reask, an_err, an_warn, an_note, an_list, an_summary, an_status)
        render_rhymes()
        render_rhythm()
        render_thesaurus()

    def refresh_analysis_stale():
        """Called after the (debounced) notes refresh: redraw only when a result flips between current and outdated."""
        sess = sessions.get(S.song.id) if S.song else None
        if not sess:
            return
        cur = LA.text_hash(lyr())
        for info, key in ((sess.analysis, "fresh"), (sess.rhyme, "rh_fresh")):
            if info and "result" in info:
                is_current = cur == info["snap"].text_hash
                if key == "rh_fresh":
                    is_current = is_current and info["snap"].prompt_version == LR.RHYME_PROMPT_VERSION
                if is_current != an_state.get(key):
                    render_analysis()
                    return
        if sess.rhythm and "result" in sess.rhythm and rhythm_fresh(sess) != an_state.get("rt_fresh"):
            render_analysis()
            return
        if sess.thes and "result" in sess.thes and thes_fresh(sess) != an_state.get("th_fresh"):
            render_analysis()

    def open_edit_syllables(eff: LA.Effective):
        """Edit one line's analysis (analysis data only: the lyrics, caret, scroll and undo history are not touched)."""
        if not S.song:
            return
        song_id, lyr_hash = S.song.id, LA.text_hash(lyr())
        ai_units = [u for sg in eff.segments for u in sg.units]
        corr = eff.correction
        units_tf = ft.TextField(label="Manual Segmentation  (separate units with | )", value=" | ".join(corr.units) if (corr and corr.units) else " | ".join(ai_units),
                                **field_style(text_size=13, height=48))
        count_tf = ft.TextField(label="Count Override  (empty = Auto)", value="" if (not corr or corr.count is None) else str(corr.count), hint_text="Auto", width=200,
                                **field_style(text_size=13, height=48))
        preview = ft.Text("", size=13, weight=ft.FontWeight.BOLD, color=C["text1"])
        warn = ft.Text("", size=11, color=C["amber"])
        err = ft.Text("", size=11, color=C["red"])
        apply_btn = ft.FilledButton("Apply", style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))
        state = {"corr": None, "ok": True}

        def recompute(ev=None):
            err.value = warn.value = ""
            try:
                units = LA.parse_manual_units(units_tf.value)
                cnt = LA.parse_manual_count(count_tf.value)
            except LA.CorrectionError as ex:
                err.value, state["ok"], apply_btn.disabled = str(ex), False, True
                preview.value = "Preview Count: —"
                safe_update(err, warn, preview, apply_btn)
                return
            if units == ai_units:
                units = None                                         # the same as the AI's own split is not a correction
            c = LA.Correction(units, cnt, eff.line.text) if (units is not None or cnt is not None) else None
            n, src = LA.effective_count(len(ai_units), c)
            preview.value = f"Preview Count: {n}   ({_SRC[src]})"
            warn.value = LA.effective_warning(len(ai_units), c)
            state["corr"], state["ok"], apply_btn.disabled = c, True, False
            safe_update(err, warn, preview, apply_btn)
        units_tf.on_change = recompute
        count_tf.on_change = recompute

        def still_valid() -> bool:
            if not (S.song and S.song.id == song_id and LA.text_hash(lyr()) == lyr_hash):
                page.pop_dialog()
                toast("The lyrics changed while the dialog was open. Nothing was changed.", C["amber"])
                return False
            return True

        def save(c):
            sess = sessions.get(song_id)
            if sess is None:
                return
            if c is None:
                sess.an_corr.pop(eff.line_id, None)
            else:
                sess.an_corr[eff.line_id] = c
            persist_analysis(sess)
            render_analysis()

        def do_apply(ev):
            if state["ok"] and still_valid():
                page.pop_dialog()
                save(state["corr"])

        def do_reset(ev):
            if still_valid():
                page.pop_dialog()
                save(None)
        apply_btn.on_click = do_apply
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Text("Edit Syllables"), content=ft.Container(width=440, content=ft.Column([
            ft.Text("Lyrics", size=11, color=C["text3"]), ft.Text(eff.line.text, size=14, color=C["text1"], selectable=True),
            ft.Text("AI Segmentation (an estimate)", size=11, color=C["text3"]), ft.Text(" | ".join(ai_units) + f"    → {eff.ai_count}", size=13, color=C["accent"], selectable=True),
            units_tf, count_tf, preview, warn, err], spacing=6, tight=True, scroll=ft.ScrollMode.AUTO)),
            actions=[ft.TextButton("Reset to AI", on_click=do_reset, style=ft.ButtonStyle(color=C["red"])), ft.TextButton("Cancel", on_click=lambda ev: page.pop_dialog()), apply_btn]))
        recompute()

    async def run_analysis(force: bool = False):
        """Analyze Lyrics: the saved result is used when the lyrics, backend, model and parameters are the same (no new AI request). `force` asks the AI again."""
        if not S.song:
            return
        if S.an_busy:
            toast("An analysis is already running"); return
        if S.chat_busy_song:
            toast("The AI is still answering in Chat. Try again in a moment.", C["amber"]); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        song_id, text, backend, host = S.song.id, lyr(), S.backend, S.host
        sess = sessions.get(song_id)
        model, params = an_identity(backend, "syl")
        snap = LA.Snapshot(song_id, text, _ui["epoch"], backend, model, params)
        lines = LA.sung_lines(text)
        at = time.strftime("%H:%M:%S")

        def failed(message: str):
            old = dict(sess.analysis or {})
            old["last_error"] = {"text_hash": snap.text_hash, "message": message, "at": at, "backend": backend}      # an earlier good result is kept; the failure sits beside it
            sess.analysis = old
            render_analysis()
        if not lines:
            failed("There are no sung lines to analyze (only [tags] or empty)."); return
        if not force:
            hit = an_cache.get(snap.cache_key)
            if hit is not None:
                sess.analysis = {"snap": snap, "result": hit, "cached": True, "at": at}
                persist_analysis(sess)
                render_analysis()
                (None if rep_busy() else toast("Showing the saved result: no new AI request.")); return
        S.an_busy, S.an_kind, S.an_progress = song_id, "syl", "Asking the AI…"
        render_analysis()
        loop = asyncio.get_running_loop()

        ask = spec_ask("songwriter", an_temp("syl"), "syl")                      # one request of the Songwriter AI's own session

        def progress(k: int, n: int):
            S.an_progress = f"Asking the AI… (part {k} of {n})" if n > 1 else "Asking the AI…"
            loop.call_soon_threadsafe(render_analysis)
        result, error = None, ""
        try:
            result = await loop.run_in_executor(None, lambda: LA.analyze(lines, ask, progress=progress))
        except LA.AnalysisError as ex:
            error = str(ex)
            log.warning("analysis failed: %s | raw=%r", ex, (ex.raw or "")[:6000])
        except Exception as ex:
            error = f"AI error: {ex}"
            log.debug("analysis AI error: %r", ex)
        finally:
            S.an_busy, S.an_kind, S.an_progress = "", "", ""
        if result is not None:
            log.debug("analysis raw answers: %r", result.raw)
            an_cache.put(snap.cache_key, result)                        # only a COMPLETE, validated result is ever cached: a failed batch raises before this point
        sess = sessions.get(snap.song_id)
        if sess is None:
            render_analysis(); return                                    # the tab was closed meanwhile
        if sess.song.lyrics != snap.text:                                # edited while waiting: this answer describes OLD lyrics
            log.debug("analysis for %s dropped: lyrics changed while waiting", snap.song_id)
            if S.song and S.song.id == snap.song_id:
                toast("The lyrics changed while the AI was working, so that result was not used. Press Analyze Lyrics again.", C["amber"])
            render_analysis(); return
        if _ui["epoch"] != snap.epoch:
            log.debug("analysis for %s finished after a tab switch (epoch %s -> %s); song and text are unchanged, so it is kept for that song", snap.song_id, snap.epoch, _ui["epoch"])
        if result is None:
            failed(error); return
        sess.analysis = {"snap": snap, "result": result, "at": at}
        persist_analysis(sess)
        render_analysis()

    # Rhyme analysis (Phase 3): the AI judges the sound, Python checks lines and words. Own result, own cache, own saved block; it never reads or changes the syllable result or the
    # manual corrections, and it is read-only for the lyrics (no highlight, no selection, no scroll) ------------------------------------------------------------------------------------
    rh_ring = ft.ProgressRing(width=14, height=14, stroke_width=2, visible=False)
    rh_status = ft.Text("", size=11, color=C["text3"])
    rh_warn = ft.Text("", size=11, color=C["amber"], visible=False)
    rh_err = ft.Text("", size=11, color=C["red"], selectable=True, visible=False)
    rh_note = ft.Text("", size=11, color=C["text3"], visible=False, selectable=True)
    rh_summary = ft.Text("", size=12, weight=ft.FontWeight.W_600, color=C["text1"])
    rh_list = ft.Column([], spacing=6)
    rh_btn = ft.FilledButton("Analyze Rhymes", icon=ft.Icons.COMPARE_ARROWS_ROUNDED, height=36, on_click=lambda e: page.run_task(run_rhymes, False),
                             style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF", shape=ft.RoundedRectangleBorder(radius=8)))
    rh_reask = ibtn(ft.Icons.REFRESH_ROUNDED, "Refresh: ask the AI again (does not use the saved result)", lambda e: page.run_task(run_rhymes, True), 18)
    rh_reask.visible = False
    rh_run = {"token": 0}                                  # one number per request: an answer that comes back for an older number (cancelled, or overtaken by a new request) is dropped
    rh_sel = {"key": None}                                 # (start, end) of the coloured word the user clicked: its pairs are shown and both ends are highlighted

    def rh_cancel_run(_e=None):
        rh_run["token"] += 1
        s_ = sessions.get(S.song.id) if S.song else None
        if s_ is not None and (s_.rhyme or {}).get("partial"):
            s_.rhyme = rh_run.get("prior")                 # the half-built result of a cancelled run is not kept
        if S.an_kind == "rhyme":
            S.an_busy, S.an_kind, S.an_progress = "", "", ""
        toast("The rhyme analysis was cancelled. An answer that arrives later is not used.")
        render_analysis()
    rh_cancel = ibtn(ft.Icons.CLOSE_ROUNDED, "Cancel this analysis (an answer that arrives later is not used)", rh_cancel_run, 18)
    rh_cancel.visible = False
    _RH_COLOR = {"end": C["accent"], "cross": C["accent"], "internal": C["green"], "near": C["amber"]}

    def _rh_card(p: "LR.RhymePair", texts: list):
        a_line, a_word, b_line, b_word = (p.a_line, p.a_word, p.b_line, p.b_word) if p.a_line <= p.b_line else (p.b_line, p.b_word, p.a_line, p.a_word)
        langs = " + ".join(LANG_NAMES.get(c, c.upper()) for c in p.lang.split("+"))
        verify = getattr(p, "verify", "unverified")
        v_label = {"verified": "Verified", "unverified": "Unverified", "rejected": "Rejected", "llm_exact": "LLM-assessed Exact", "llm_near": "LLM-assessed Near", "uncertain": "Uncertain"}.get(verify, "Uncertain")
        v_col = C["green"] if verify in ("verified", "llm_exact") else (C["red"] if verify == "rejected" else C["amber"])

        def side(num, word):
            return ft.Column([ft.Text(f"Line {num}: {word}", size=13, weight=ft.FontWeight.BOLD, color=C["text1"], selectable=True),
                              ft.Text(texts[num - 1] if 0 < num <= len(texts) else "", size=11, color=C["text3"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)], spacing=0)
        if a_line == b_line:
            body = [ft.Text(f"Line {a_line}", size=11, color=C["text3"]), ft.Text(f"{a_word}  ↔  {b_word}", size=13, weight=ft.FontWeight.BOLD, color=C["text1"], selectable=True),
                    ft.Text(texts[a_line - 1] if 0 < a_line <= len(texts) else "", size=11, color=C["text3"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)]
        else:
            body = [side(a_line, a_word), ft.Text("↕", size=14, color=_RH_COLOR[p.type]), side(b_line, b_word)]
        body.append(ft.Row([ft.Text(LR.TYPE_TITLES.get(p.type, p.type), size=11, weight=ft.FontWeight.W_600, color=_RH_COLOR.get(p.type, C["text2"])),
                            ft.Text(f"· {langs} ·", size=11, color=C["text3"]),
                            ft.Text(v_label, size=11, weight=ft.FontWeight.W_600, color=v_col),
                            ft.Text(f"AI: {p.confidence}", size=11, color=C["text3"])], spacing=4))
        extra = " · ".join(x for x in (getattr(p, "sound_type", "").replace("_", " "), getattr(p, "placement_detail", "").replace("_", " "), getattr(p, "traditional_role", "")) if x)
        if extra:
            body.append(ft.Text(extra + (f" · {p.pair_id}" if getattr(p, "pair_id", "") else "") + (f" · group {p.group_id}" if getattr(p, "group_id", "") else ""), size=11, color=C["text2"]))
        if getattr(p, "position_validated", False):
            body.append(ft.Text("Position checked by Python" + (" · the AI's index was corrected" if p.position_fixed else ""), size=10, color=C["text3"]))
        if getattr(p, "verify_note", ""):
            body.append(ft.Text(p.verify_note, size=11, color=C["text2"], selectable=True))
        if p.reason:
            body.append(ft.Text("AI explanation: " + p.reason, size=11, color=C["text2"], selectable=True))
        return ft.Container(ft.Column(body, spacing=2), bgcolor=C["panel2"], border_radius=8, padding=po(8, 6, 8, 6),
                            border=ft.Border.all(1, C["amber"]) if verify not in ("verified", "llm_exact") else None)

    def _rc_style(rc, sp, picked: bool = False):
        """How a coloured rhyme word is drawn. Verified (Thai sound check) = bold on a tint, unverified = dotted underline, near = italic with a wavy underline (bold when verified). A word the user
        clicked and its partners get a strong tint and a solid underline, so the pair is seen together."""
        col = rc.color_of(sp)
        bold = ft.FontWeight.BOLD if sp.verify in ("verified", "llm_exact", "llm_near") else None
        if picked:
            return ft.TextStyle(color=col, weight=ft.FontWeight.BOLD, italic=(sp.kind == "near"), bgcolor=ft.Colors.with_opacity(0.45, col), decoration=ft.TextDecoration.UNDERLINE, decoration_color=col)
        if sp.kind == "near":
            return ft.TextStyle(color=col, italic=True, weight=bold, decoration=ft.TextDecoration.UNDERLINE, decoration_color=col, decoration_style=ft.TextDecorationStyle.WAVY)
        if sp.verify in ("verified", "llm_exact"):
            return ft.TextStyle(color=col, weight=bold, bgcolor=ft.Colors.with_opacity(0.18, col))
        return ft.TextStyle(color=col, decoration=ft.TextDecoration.UNDERLINE, decoration_color=col, decoration_style=ft.TextDecorationStyle.DOTTED)

    _VER_TEXT = {"verified": "Verified by Python (Thai sound check)", "ai_assessed": "AI-assessed: nothing independent verified it", "unverified": "Unverified"}

    def rh_pick(sp):
        rh_sel["key"] = None if rh_sel["key"] == (sp.start, sp.end) else (sp.start, sp.end)
        render_rhymes()

    def _rh_detail(rc, key):
        """Every pair that the clicked word belongs to (never just one): the partner and its line, the sound type and relation, where they sit, the group, the reason, the confidence, and how
        sure the program is (the sound check and the position check are two separate facts)."""
        links = [k for k in (rc.map.links if rc.map else []) if k.colourable and key in (k.a.span, k.b.span)]
        if not links:
            return [], set()
        cards, picked = [], {key}
        mine_text = next((w.text for k in links for w in (k.a, k.b) if w.span == key), "")
        for k in links:
            mine, other = (k.a, k.b) if k.a.span == key else (k.b, k.a)
            picked.add(other.span)
            facts = " · ".join(x for x in (k.sound_type.replace("_", " ") if k.sound_type else ("vowel rhyme" if k.kind == "vowel" else "consonant rhyme"),
                                           k.placement.replace("_", " "), k.traditional_role) if x)
            sound_tag = {"exact": "EXACT", "near": "NEAR"}.get(k.sound, (k.sound or "").upper())          # the sound level of THIS pair, in front: a shared colour does not make every pair exact
            ids = " · ".join(x for x in ((f"pair {k.pair_id}" if k.pair_id else ""), (f"group {k.group}" if k.group else "")) if x)
            body = [ft.Text(f"“{mine.text}” (line {mine.file_line}) ↔ “{other.text}” (line {other.file_line})", size=12, weight=ft.FontWeight.W_600, color=C["text1"], selectable=True),
                    ft.Text(spans=[ft.TextSpan(sound_tag, style=ft.TextStyle(weight=ft.FontWeight.BOLD, italic=(k.sound == "near"), color=C["green"] if k.sound == "exact" else C["amber"])),
                                   ft.TextSpan(("  ·  " + facts) if facts else "", style=ft.TextStyle(color=C["text2"]))], size=11)]
            if ids:
                body.append(ft.Text(ids, size=10, color=C["text3"]))
            if k.reason:
                body.append(ft.Text(k.reason, size=11, color=C["text2"], selectable=True))
            body.append(ft.Text(f"{_VER_TEXT.get(k.verification, k.verification)}" + (f" · AI confidence: {k.confidence}" if k.confidence else "")
                                + (" · position checked" if k.position_validated else "") + (" · the AI's index was corrected" if k.position_fixed else ""), size=10, color=C["text3"]))
            cards.append(ft.Container(ft.Column(body, spacing=2), bgcolor=C["panel"], border_radius=6, padding=po(8, 5, 8, 5)))
        head = ft.Text(f"“{mine_text}”: {len(links)} relation(s)" if len(links) > 1 else f"“{mine_text}”", size=11, weight=ft.FontWeight.BOLD, color=C["text2"])
        return [head] + cards, picked

    def _rc_preview(res):
        """The lyrics with the rhyme words coloured (a preview beside the pairs: the editor itself cannot colour single words). Colours come from RhymeColors, so the same song always gets the same colours."""
        rc = RC.colorize(lyr(), res, song_lang(), ins=li_cached(lyr()))
        if not rc.groups:
            return None
        rows = []
        detail, picked = _rh_detail(rc, rh_sel["key"]) if rh_sel["key"] else ([], set())
        if rh_sel["key"] and not detail:
            rh_sel["key"] = None
        for n, segs in RC.line_segments(lyr(), rc):
            if not segs:
                continue
            tag = len(segs) == 1 and segs[0][1] is None and RC.is_tag_line(segs[0][0])
            spans = [ft.TextSpan(t, style=_rc_style(rc, sp, (sp.start, sp.end) in picked) if sp else ft.TextStyle(color=C["text3"] if tag else C["text1"]),
                                 on_click=(lambda e, sp=sp: rh_pick(sp)) if sp else None) for t, sp in segs]
            rows.append(ft.Row([ft.Text(str(n), size=11, color=C["text3"], width=22), ft.Text(spans=spans, size=13, expand=True)], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START))
        notes = []
        if rc.skipped:
            notes.append(ft.Text(f"{len(rc.skipped)} pair(s) could not be placed on an exact word and are not coloured.", size=10, color=C["amber"], tooltip="\n".join(rc.skipped[:8])))
        if res is None:
            notes.append(ft.Text("The colours come from the Python rhyme analysis (sound rules on the spelling). The Analyze Rhymes button is the older AI analysis; its result is not used for the colours." if FF.PYTHON_RHYME_PRIMARY else "Press Analyze Rhymes: the AI listens to the line endings and says which lines rhyme.", size=10, color=C["text3"]))
        return ft.Container(ft.Column([
            ft.Text("RHYME COLORS", size=11, weight=ft.FontWeight.BOLD, color=C["text2"]),
            ft.Text(("Words that rhyme together share a colour. Bold on a tint = exact rhyme read by Python · wavy italic = near rhyme · dotted underline = uncertain. "
                     "Python reads the spelling with sound rules: it can miss internal rhymes and words of several syllables, and English is not confirmed by sound. A preview only: your lyrics are not changed.") if FF.PYTHON_RHYME_PRIMARY else
                    ("Words that rhyme together share a colour. Bold on a tint = the AI assessed an exact rhyme · wavy italic = near rhyme · dotted underline = uncertain. "
                     "These are the AI's assessments: nothing independent verified them. A preview only: your lyrics are not changed."), size=10, color=C["text3"]),
            *rows, *notes,
            *([ft.Divider(height=6, color=C["border"])] + detail if detail else [ft.Text("Click a coloured word to see which words it rhymes with and why.", size=10, color=C["text3"])])], spacing=3),
            bgcolor=C["panel2"], border_radius=8, padding=po(10, 8, 10, 8))

    def section_id_at(first_file_line: int) -> str:
        """The id (s1, s2 ...) of the section whose first sung line is on this file line of the open lyrics."""
        for ln in LA.sung_lines(lyr()):
            if ln.file_line == first_file_line:
                return R6.section_id(ln.section)
        return ""

    class _Problems(list):
        """The sentences about one section; `unreadable` = none of its lines got an end group at all."""
        unreadable = False

    def _section_unreadable(sec: dict) -> bool:
        lines_ = sec.get("lines") or []
        return bool(lines_) and not any(x.get("reported") for x in lines_)

    def recheck_problems(res) -> dict:
        """{section id: what Python found wrong INSIDE the Analyzer's answer for it} for the open lyrics. Python only compares the parts of the answer with each other; no sound is judged. Nothing for a result
        that was not made step by step (it has no saved steps to ask about)."""
        try:
            if not (res is not None and (getattr(res, "llm", None) or {}).get("steps")):
                return {}
            ev = RF.build_llm_evidence(res.llm, lyr())
            bodies = {sid_: b for win_ in res.llm["steps"] for sid_, b in win_}
            out = {}
            for sec in (ev or {}).get("sections") or []:
                sid = section_id_at(sec["first"])
                if not sid:
                    continue
                dup = RS.duplicate_sounds(bodies.get(sid, ""))                     # the same end sound in two [END] tags, read from the answer itself
                pr = _Problems(dup + [p for p in RF.section_problems(sec) if not (dup and p.startswith("the end sound"))])
                pr.unreadable = _section_unreadable(sec)
                if pr.unreadable and not pr:
                    pr.append("no end group was read for any line of this section")
                if pr:
                    out[sid] = pr
            return out
        except Exception as ex:
            log.debug("section problems could not be computed: %r", ex)
            return {}

    def _recheck_box(sid: str, problems: list, reason: str = "", unreadable: bool = False):
        """ONE short status for a section of the Analyzer's answer that is not complete, the button that asks the Analyzer to check that section again (a new conversation), and the details closed behind a
        Details row. Every detail is also written to the log; nothing that was rejected is shown as a confirmed result."""
        log.debug("rhyme section %s: %d problem(s) in the Analyzer's answer: %s", sid, len(problems), problems)
        col = [ft.Text(I18N.tr(CG.UNREADABLE_SECTION if unreadable else CG.PARTIAL_SHORT), size=11, weight=ft.FontWeight.BOLD, color=C["amber"])]
        detail = [ft.Text("• " + I18N.tr(p), size=10, color=C["amber"], selectable=True) for p in problems]
        if reason:
            detail.append(ft.Text("Last check, the Analyzer's reason: " + reason, size=10, color=C["text2"], selectable=True))
        if detail:
            col.append(_rep_tile(f"rcbox:{sid}", f"Details ({len(problems)})", detail))
        col.append(ft.TextButton("Re-check this section", icon=ft.Icons.REFRESH_ROUNDED, disabled=bool(S.an_busy), on_click=lambda e, i=sid: page.run_task(run_recheck, i),
                                 style=ft.ButtonStyle(color=C["accent"], padding=po(4, 0, 4, 0))))
        return ft.Container(ft.Column(col, spacing=2), padding=po(2, 4, 2, 4))

    def _rh_sections(res, fresh_lyrics: bool = False):
        """The rhyme scheme of every section (derived by Python from the validated end-to-end pairs; the AI's own scheme is shown only where it differs) with its reading, then the whole song."""
        out = []
        probs = recheck_problems(res) if fresh_lyrics else {}                      # what Python found wrong inside the Analyzer's answer, by section id
        checked = (res.llm or {}).get("rechecks") or {}
        for sd in res.sections:
            name = sd.get("name") or "(before the first tag)"
            scheme, sname = sd.get("scheme") or "", sd.get("scheme_name") or ""
            head = [ft.Text(name, size=12, weight=ft.FontWeight.W_600, color=C["text1"]),
                    ft.Container(ft.Text(scheme or "-", size=11, weight=ft.FontWeight.BOLD, color=C["accent"], font_family="Consolas"), bgcolor=C["panel"], border_radius=10, padding=po(8, 1, 8, 1)),
                    ft.Text({"none": "Unrhymed", "free": "Free / irregular"}.get(sname, sname), size=11, color=C["text3"])]
            body = [ft.Row(head, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)]
            if sd.get("scheme_differs"):
                body.append(ft.Text(f"The AI's own scheme was {sd.get('ai_scheme')}: the scheme above follows the pairs that were placed and checked.", size=10, color=C["amber"]))
            if sd.get("near_lines"):
                body.append(ft.Text("Linked only by a near rhyme (not part of the exact scheme): lines " + ", ".join(str(n) for n in sd["near_lines"]), size=10, color=C["text3"]))
            counts = sd.get("counts") or {}
            if counts:
                body.append(ft.Text(" · ".join(f"{v} {k.replace('_', ' ')}" for k, v in counts.items()), size=10, color=C["text3"]))
            if sd.get("summary"):
                body.append(ft.Text(sd["summary"], size=11, color=C["text2"], selectable=True))
            for ln_, tx in sd.get("quality") or []:
                body.append(ft.Text(f"Line {ln_}: {tx}", size=11, color=C["text2"], selectable=True))
            for ln_, tx in sd.get("uncertain") or []:
                body.append(ft.Text(f"Uncertain · Line {ln_}: {tx}", size=11, color=C["amber"], selectable=True))
            sid_ = str(sd.get("id") or "")
            if sid_ in probs:
                body.append(_recheck_box(sid_, probs[sid_], str((checked.get(sid_) or {}).get("reason") or ""), bool(getattr(probs[sid_], "unreadable", False))))
            elif sid_ in checked and checked[sid_].get("reason"):
                body.append(ft.Text("Checked again, the Analyzer's reason: " + str(checked[sid_]["reason"]), size=10, color=C["text2"], selectable=True))
            out.append(ft.Container(ft.Column(body, spacing=2), bgcolor=C["panel2"], border_radius=8, padding=po(8, 6, 8, 6)))
        ov = res.overview or {}
        if ov.get("text") or ov.get("points"):
            body = [ft.Text("SONG OVERVIEW", size=11, weight=ft.FontWeight.BOLD, color=C["text2"])]
            if ov.get("text"):
                body.append(ft.Text(ov["text"], size=11, color=C["text2"], selectable=True))
            for pt in ov.get("points") or []:
                body.append(ft.Text(f"Line {pt['line']}: {pt['text']}", size=11, color=C["text2"], selectable=True))
            body.append(ft.Text("A reading of the text only: no melody was heard.", size=10, color=C["text3"]))
            out.append(ft.Container(ft.Column(body, spacing=3), bgcolor=C["panel2"], border_radius=8, padding=po(10, 8, 10, 8)))
        return out

    def render_rhymes():
        """Show the rhyme analysis of the ACTIVE song (read-only: nothing here touches the lyrics field, its selection or its scroll)."""
        sess = sessions.get(S.song.id) if S.song else None
        info = sess.rhyme if sess else None
        busy_here = bool(S.song) and S.an_busy == S.song.id and S.an_kind == "rhyme"
        cur = LA.text_hash(lyr()) if S.song else ""
        has_result = bool(info and "result" in info)
        same_lyrics = has_result and info["snap"].text_hash == cur
        version_current = has_result and info["snap"].prompt_version == LR.RHYME_PROMPT_VERSION
        fresh = same_lyrics and version_current
        an_state["rh_fresh"] = fresh if has_result else None
        err = (info or {}).get("last_error")
        err_here = bool(err and err.get("text_hash") == cur)
        rh_ring.visible = busy_here
        rh_cancel.visible = busy_here
        rh_btn.disabled = (not S.song) or bool(S.an_busy)
        rh_reask.visible, rh_reask.disabled = has_result, rh_btn.disabled
        if err_here:
            log.debug("rhyme analysis last error (not shown on screen): %s", err.get("message"))      # the technical text stays in the log; the page shows one short line
        rh_err.value = (I18N.tr(CG.UNREADABLE) + (" " + I18N.tr("The earlier result below is kept.") if has_result else "")) if err_here else ""
        rh_err.visible = bool(rh_err.value)
        rh_warn.value = ("The lyrics changed after this analysis. Press Analyze Rhymes to update it. The pairs below belong to the old lyrics."
                         if has_result and not same_lyrics else
                         "This analysis is from the older line-endings analyzer. Press Analyze Rhymes to run the Rhyme Analyzer AI (schemes, positions and a reading of the song)."
                         if has_result and not version_current else "")
        rh_warn.visible = bool(rh_warn.value)
        rows, summary, status, note = [], "", "", ""
        if busy_here:
            status = S.an_progress or "Asking the AI…"
        preview = _rc_preview(info["result"] if same_lyrics else None) if S.song else None          # the sound groups need no AI result; the AI pairs join them when the result is for these lyrics
        if preview is not None:
            rows.append(preview)
        if has_result:
            res: LR.RhymeResult = info["result"]
            if res.schema and res.sections:
                rows.append(ft.Text("RHYME SCHEME BY SECTION", size=11, weight=ft.FontWeight.BOLD, color=C["text2"]))
                rows += _rh_sections(res, same_lyrics)
            for t, title in (("end", "END RHYMES"), ("cross", "CROSS-LINE RHYMES"), ("internal", "INTERNAL RHYMES"), ("near", "NEAR RHYMES")):
                rows.append(ft.Text(title, size=11, weight=ft.FontWeight.BOLD, color=_RH_COLOR[t]))
                ps = res.of_type(t)
                rows += [_rh_card(p, res.lines) for p in ps] if ps else [ft.Text("No matches found", size=11, color=C["text3"])]
            if res.rejected:
                rows.append(ft.Text("NOT A RHYME", size=11, weight=ft.FontWeight.BOLD, color=C["red"]))
                rows += [_rh_card(p, res.lines) for p in res.rejected]
            llm = any(p.verify in ("llm_exact", "llm_near", "uncertain") for p in res.pairs)
            if llm:
                summary = (f"{sum(1 for p in res.pairs if p.verify == 'llm_exact')} LLM-assessed exact · {sum(1 for p in res.pairs if p.verify == 'llm_near')} LLM-assessed near · "
                           f"{sum(1 for p in res.pairs if p.verify == 'uncertain')} uncertain · the AI judged the sound; nothing independent verified it")
            else:
                n_ver = sum(1 for p in res.pairs if p.verify == "verified")
                n_un = sum(1 for p in res.pairs if p.verify != "verified")
                summary = f"{n_ver} verified · {n_un} unverified" + (f" · {len(res.rejected)} rejected" if res.rejected else "") + " · Verified is a Thai spelling check, not the AI confidence"
            if not busy_here:
                status = f"{info['snap'].backend.replace('local:', 'Local: ')} · {info.get('at', '')}" + (" · saved result" if (info.get("cached") or info.get("loaded")) else "") + ("" if fresh else " · outdated")
            if res.ignored:
                log.debug("rhyme ignored on screen: %s", res.ignored)
                note = I18N.tr(CG.PARTIAL_SHORT)
        elif err_here and not busy_here:
            status = "The rhyme analysis failed"
        if not rows and not busy_here and not rh_err.value:
            rows = [ft.Text("Press Analyze Rhymes: the AI listens for end, cross-line, internal and near rhymes and reports the groups and pairs it hears. "
                            "The program reads and checks the format of that answer, names the groups A, B, C in the order the AI reported them, and does not change the AI's judgement of the sounds. "
                            "A line shown with a grey dash has no rhyme group in the AI's answer; that is missing information, not a statement that the line has no rhyme.", size=12, color=C["text3"])]
        rh_list.controls = rows
        rh_note.value, rh_note.visible = note, bool(note)
        rh_summary.value, rh_status.value = summary, status
        safe_update(rh_ring, rh_btn, rh_reask, rh_cancel, rh_err, rh_warn, rh_note, rh_list, rh_summary, rh_status)

    async def run_rhymes(force: bool = False):
        """Analyze Rhymes: same lyrics + backend + model + parameters + prompt gives the saved result (no new AI request); `force` (Refresh) asks the AI again."""
        if not S.song:
            return
        if S.an_busy:
            toast("An analysis is already running"); return
        if S.chat_busy_song:
            toast("The AI is still answering in Chat. Try again in a moment.", C["amber"]); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        song_id, text, backend, host = S.song.id, lyr(), S.backend, S.host
        sess = sessions.get(song_id)
        model, params = an_identity(backend, "rhyme")
        snap = LA.Snapshot(song_id, text, _ui["epoch"], backend, model, f"{params},w={R6.WINDOW_MAX}/{R6.CONTEXT_LINES},lang={song_lang()},schema={R6.SCHEMA},mode={'steps' if (RS.ENABLED and backend != 'grok') else 'one'},ph={LR.prompt_hash()}", LR.RHYME_PROMPT_VERSION, kind=LR.KIND)
        lines = LA.sung_lines(text)
        at = time.strftime("%H:%M:%S")

        def failed(message: str):
            old = dict(sess.rhyme or {})
            old["last_error"] = {"text_hash": snap.text_hash, "message": message, "at": at, "backend": backend}      # an earlier good result is kept; the failure sits beside it
            sess.rhyme = old
            render_analysis()
        if not lines:
            failed("There are no sung lines to analyze (only [tags] or empty)."); return
        if not force:
            hit = rh_cache.get(snap.cache_key)
            if hit is not None:
                sess.rhyme = {"snap": snap, "result": hit, "cached": True, "at": at}
                persist_analysis(sess)
                render_analysis()
                (None if rep_busy() else toast("Showing the saved result: no new AI request.")); return
        S.an_busy, S.an_kind, S.an_progress = song_id, "rhyme", "Asking the AI…"
        render_analysis()
        loop = asyncio.get_running_loop()
        rh_run["token"] += 1
        my_run = rh_run["token"]
        lang0 = song_lang()                                                          # the lyrics language of the song, read now (the analysis runs in another thread)
        steps_mode = RS.ENABLED and backend != "grok"                                # Grok reads the whole song in ONE request (one window); the local model is asked step by step
        win_size = max(R6.WINDOW_MAX, len(lines)) if backend == "grok" else R6.WINDOW_MAX
        try:
            sys_prompt = LR.step_system_prompt() if steps_mode else LR.system_prompt()      # rhyme_analyzer.md + the program's answer format; an invalid prompt file sends nothing
        except LA.AnalysisError as ex:
            S.an_busy, S.an_kind, S.an_progress = "", "", ""
            failed(str(ex)); return
        temp0 = an_temp("rhyme")
        ask = lambda system, user: spec_llm(system, user, backend, host, temp0)      # one-shot mode: stateless, no specialist session, no chat history (the Rhyme Analyzer is not a chat)
        prior = sess.rhyme if sess else None
        rh_run["prior"] = prior
        asker = None
        if steps_mode:                                                               # step by step: the lyrics, an overview, then one section at a time; each part is shown as soon as it is read
            def _live_status(txt: str):
                S.an_progress = txt
                loop.call_soon_threadsafe(render_analysis)

            def _apply_part(part):
                s_ = sessions.get(snap.song_id)
                if rh_run["token"] != my_run or s_ is None or s_.song.lyrics != snap.text:
                    return
                s_.rhyme = {"snap": snap, "result": part, "at": at, "partial": True}
                render_analysis()

            def _show_part():
                if rh_run["token"] != my_run:
                    return
                part = asker.partial_result(LR.analyze_rhymes, lines, lang0, sys_prompt)      # Python only: the same reader over the sections answered so far
                loop.call_soon_threadsafe(_apply_part, part)
            asker = RS.StepAsker(RS.conversation_factory(backend, host, LLM_DIR, temp0), _live_status, lambda: rh_run["token"] != my_run, _show_part)
            ask = asker

        def progress(k: int, n: int):
            S.an_progress = f"Asking the AI… (part {k} of {n})" if n > 1 else "Asking the AI…"
            loop.call_soon_threadsafe(render_analysis)
        result, error = None, ""
        t_ai = time.perf_counter()
        try:
            result = await loop.run_in_executor(None, lambda: LR.analyze_rhymes(lines, ask, size=win_size, progress=progress, language=lang0, system=sys_prompt))
        except LA.AnalysisError as ex:
            error = str(ex)
            log.warning("rhyme analysis failed: %s | raw=%r", ex, (ex.raw or "")[:6000])
        except Exception as ex:
            error = f"AI error: {ex}"
            log.debug("rhyme analysis AI error: %r", ex)
        finally:
            if rh_run["token"] == my_run:
                S.an_busy, S.an_kind, S.an_progress = "", "", ""
        if rh_run["token"] != my_run:                                                # cancelled, or a newer request started: this answer is not used
            log.debug("rhyme analysis answer dropped: cancelled or overtaken")
            s_ = sessions.get(snap.song_id)
            if s_ is not None and (s_.rhyme or {}).get("partial") and rh_run.get("prior") is prior:
                s_.rhyme = prior                                                     # the half-built result of this run is not kept
            return
        log.info("rhyme analysis: %d sung lines, %d answer(s), %.1f s, %s", len(lines), len(result.raw) if result is not None else 0, time.perf_counter() - t_ai, "ok" if result is not None else "failed")
        if result is not None:
            log.debug("rhyme raw answers: %r | parsed: %r | ignored: %r", result.raw, [(p.type, p.a_line, p.a_word, p.b_line, p.b_word, p.confidence) for p in result.pairs], result.ignored)
            rh_cache.put(snap.cache_key, result)                        # only a COMPLETE, validated result is ever cached: a failed window raises before this point
        sess = sessions.get(snap.song_id)
        if sess is None:
            render_analysis(); return                                    # the tab was closed meanwhile
        if sess.song.lyrics != snap.text:                                # edited while waiting: this answer describes OLD lyrics
            log.debug("rhyme analysis for %s dropped: lyrics changed while waiting", snap.song_id)
            if (sess.rhyme or {}).get("partial"):
                sess.rhyme = prior
            if S.song and S.song.id == snap.song_id:
                toast("The lyrics changed while the AI was working, so that result was not used. Press Analyze Rhymes again.", C["amber"])
            render_analysis(); return
        if result is None:
            if (sess.rhyme or {}).get("partial"):
                sess.rhyme = prior                                           # the half-built result is dropped; an earlier good result is kept, the failure sits beside it
            failed(error); return
        if asker is not None:
            if asker.problems:
                log.debug("rhyme steps problems: %s", asker.problems)
            result.overview = {"text": asker.overview_text, "points": []}
            result.llm["steps"] = asker.window_items                       # the answer of every section as it was given: one section can be asked again later
        sess.rhyme = {"snap": snap, "result": result, "at": at}
        persist_analysis(sess)
        render_analysis()

    async def run_recheck(section_id: str):
        """Ask the Rhyme Analyzer to check ONE section of its own answer again, in a NEW conversation (system prompt, the lyrics, then its earlier answer for that section with what Python found wrong in
        it). The new answer and its reason replace that section only; the rest of the result stays. Only for a result that was made step by step and still matches the open lyrics."""
        if not S.song:
            return
        if S.an_busy or rep_busy() or S.chat_busy_song:
            toast("The AI is busy. Try again in a moment.", C["amber"]); return
        song_id, backend, host = S.song.id, S.backend, S.host
        sess_ = sessions.get(song_id)
        info = sess_.rhyme if sess_ else None
        if not backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        if not info or "result" not in info or info.get("partial") or info["snap"].text_hash != LA.text_hash(lyr()):
            toast("Press Analyze Rhymes first: there is no finished result for these lyrics.", C["amber"]); return
        res, snap = info["result"], info["snap"]
        steps = (res.llm or {}).get("steps")
        problems = recheck_problems(res).get(section_id)
        if not steps or not problems:
            toast("Nothing to check again in this section (or the result was not made step by step).", C["amber"]); return
        lines, lang0 = LA.sung_lines(lyr()), song_lang()
        try:
            sys_prompt = LR.step_system_prompt()
        except LA.AnalysisError as ex:
            toast(str(ex), C["amber"]); return
        w = RS.window_of(steps, section_id)
        wins = R6.windows(lines, R6.WINDOW_MAX, R6.CONTEXT_LINES)
        if w < 0 or w >= len(wins):
            toast("This section could not be found in the saved result. Press Analyze Rhymes again.", C["amber"]); return
        window_user = R6.build_request(wins[w][0], wins[w][1], lang0)
        old_body = next((b for sid_, b in steps[w] if sid_ == section_id), "")
        make = RS.conversation_factory(backend, host, LLM_DIR, an_temp("rhyme"))
        rh_run["token"] += 1
        my_run = rh_run["token"]
        rh_run["prior"] = info
        S.an_busy, S.an_kind, S.an_progress = song_id, "rhyme", "Checking the section again…"
        render_analysis()
        loop = asyncio.get_running_loop()

        def status(txt: str):
            S.an_progress = txt
            loop.call_soon_threadsafe(render_analysis)

        def work():
            new_body, reason = RS.recheck_section(make, sys_prompt, window_user, section_id, old_body, problems, lambda: rh_run["token"] != my_run, status)
            new_steps = [[[sid_, new_body if sid_ == section_id else b] for sid_, b in win_] for win_ in steps]
            new_res = RS.rebuild_result(LR.analyze_rhymes, lines, new_steps, lang0, sys_prompt)
            return new_res, new_steps, reason
        out, error = None, ""
        try:
            out = await loop.run_in_executor(None, work)
        except RS.Stopped:
            pass
        except Exception as ex:
            error = str(ex)
            log.warning("section re-check failed: %r", ex)
        finally:
            if rh_run["token"] == my_run:
                S.an_busy, S.an_kind, S.an_progress = "", "", ""
        sess_ = sessions.get(song_id)
        if rh_run["token"] != my_run or sess_ is None or sess_.song.lyrics != snap.text:
            render_analysis(); return                                       # cancelled, or the lyrics changed meanwhile: nothing is replaced
        if out is None:
            toast("The section could not be checked again; the earlier result is kept.", C["amber"])
            render_analysis(); return
        new_res, new_steps, reason = out
        new_res.overview = dict(res.overview or {})
        new_res.llm["steps"] = new_steps
        rc = dict((res.llm or {}).get("rechecks") or {})
        rc[section_id] = {"reason": reason, "problems": problems, "at": time.strftime("%H:%M:%S")}
        new_res.llm["rechecks"] = rc
        sess_.rhyme = {"snap": snap, "result": new_res, "at": time.strftime("%H:%M:%S")}
        rh_cache.put(snap.cache_key, new_res)
        persist_analysis(sess_)
        render_analysis()
        left = recheck_problems(new_res).get(section_id)
        toast("The section was checked again and replaced. Write the report again to use it." if not left else "The section was checked again, but its answer still disagrees with itself.", C["green"] if not left else C["amber"])

    # Rhythm & singability (Phase 4): Python compares the EFFECTIVE syllable counts (manual count > manual segmentation > AI), the AI judges phrasing and flow from the text only.
    # Own result, cache and saved block. It never calls the AI to count, never writes a count, and its suggested wording is shown apart from the lyrics and never applied. ----------
    rt_ring = ft.ProgressRing(width=14, height=14, stroke_width=2, visible=False)
    rt_status = ft.Text("", size=11, color=C["text3"])
    rt_warn = ft.Text("", size=11, color=C["amber"], visible=False)
    rt_err = ft.Text("", size=11, color=C["red"], selectable=True, visible=False)
    rt_note = ft.Text("", size=11, color=C["text3"], visible=False, selectable=True)
    rt_summary = ft.Text("", size=11, color=C["text2"])
    rt_list = ft.ListView(expand=True, spacing=6, padding=po(0, 0, 4, 0))
    rt_btn = ft.FilledButton("Analyze Rhythm", icon=ft.Icons.GRAPHIC_EQ_ROUNDED, height=36, on_click=lambda e: page.run_task(run_rhythm, False),
                             style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF", shape=ft.RoundedRectangleBorder(radius=8)))
    rt_reask = ibtn(ft.Icons.REFRESH_ROUNDED, "Refresh: ask the AI again (does not use the saved result)", lambda e: page.run_task(run_rhythm, True), 18)
    rt_reask.visible = False

    def rhythm_lines(sess):
        """RLine for the CURRENT lyrics from the effective syllable counts, or None when there is no syllable result for exactly these lyrics (the AI is never asked to count)."""
        info = sess.analysis if sess else None
        if not (info and "result" in info and info["snap"].text_hash == LA.text_hash(lyr())):
            return None
        effs, _ = LA.effective_lines(info["result"], sess.an_corr)
        return RT.build_lines(effs, lyr())

    def rhythm_fresh(sess) -> bool:
        info = sess.rhythm if sess else None
        if not (info and "result" in info and info["snap"].text_hash == LA.text_hash(lyr())):
            return False
        if info["snap"].prompt_version != RT.RHYTHM_PROMPT_VERSION:
            return False
        rl = rhythm_lines(sess)
        return rl is not None and RT.counts_hash(rl) == info["result"].counts_hash

    def _rt_unit(l) -> str:
        return {"mora": "mora", "mixed": "units (mora + syllables)"}.get(l.unit, "syllable" if l.count == 1 else "syllables")

    def _rt_structure(rl: list) -> list:
        groups = RT.group_sections(rl)
        maxc = max([l.count for l in rl] + [1])
        out = []
        for g in groups:
            head = f"{g.name.upper()}  ·  {g.total} in total" + (f"  ·  middle value {g.median:g}" if g.comparable and len(g.lines) >= 3 else "")
            out.append(ft.Text(head, size=11, weight=ft.FontWeight.BOLD, color=C["text2"]))
            for l in g.lines:
                flag = g.flagged.get(l.num)
                col = C["amber"] if flag is not None else C["accent"]
                bar = ft.Container(width=max(2, 80 * l.count / maxc), height=6, bgcolor=col, border_radius=3)
                row = ft.Row([ft.Text(f"Line {l.num}", size=11, color=C["text3"], width=42), ft.Text(f"{l.count} {_rt_unit(l)}", size=11, color=C["text1"], width=96 if l.unit == "mixed" else 70), bar,
                              ft.Text("✎" if l.source != "ai" else "", size=11, color=C["amber"], tooltip="Your manual count / segmentation is used" if l.source != "ai" else None)],
                             spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)
                body = [row]
                if flag is not None:
                    body.append(ft.Text(f"{flag:+g} compared with the middle of this section. Not an error: the melody may give this line more or fewer notes.", size=11, color=C["amber"]))
                out.append(ft.Container(ft.Column(body, spacing=1), padding=po(2, 0, 0, 0), tooltip=l.text))
            if not g.comparable:
                out.append(ft.Text("This section mixes mora (Japanese) and syllables. They are not one unit, so its lines are not compared with each other.", size=11, color=C["text3"]))
        sims = RT.similar_sections(groups)
        if sims:
            out.append(ft.Text("SIMILAR SECTIONS", size=11, weight=ft.FontWeight.BOLD, color=C["text2"]))
            for a, b, diffs, tot in sims:
                txt = f"{a.name} ({a.total}) vs {b.name} ({b.total}): " + ("same line lengths" if not diffs else "; ".join(f"line {x} → {y}: {d:+d}" for x, y, d in diffs[:6]))
                out.append(ft.Text(txt, size=11, color=C["text2"]))
        return out

    def _rt_item_card(num: int, text: str, its: list):
        body = [ft.Text(f"Line {num}", size=11, color=C["text3"]), ft.Text(text, size=12, color=C["text1"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)]
        for it in its:
            sev_col = C["accent"] if it.severity == "suggestion" else (C["amber"] if it.severity == "uncertain" else C["text3"])
            body.append(ft.Row([ft.Text(RT.ISSUE_TITLES[it.issue], size=11, weight=ft.FontWeight.W_600, color=C["text1"]), ft.Text(it.severity, size=11, weight=ft.FontWeight.W_600, color=sev_col)], spacing=6))
            body.append(ft.Text(it.reason, size=11, color=C["text2"], selectable=True))
            if it.suggestion:
                if it.issue == "breath_point":
                    body.append(ft.Text(f"Possible pause after: “{it.suggestion}”", size=11, color=C["text2"]))
                else:
                    body.append(ft.Container(ft.Column([ft.Text("Suggested wording (AI proposal, not applied)", size=11, color=C["amber"]),
                                                        ft.Text(it.suggestion, size=11, italic=True, color=C["text1"], selectable=True)], spacing=1),
                                             bgcolor=C["field"], border_radius=6, padding=po(6, 4, 6, 4), border=ft.Border.all(1, C["border"])))
        return ft.Container(ft.Column(body, spacing=2), bgcolor=C["panel2"], border_radius=8, padding=po(8, 6, 8, 6))

    def render_rhythm():
        """Show the rhythm analysis of the ACTIVE song (read-only for the lyrics: nothing here touches the lyrics field, its selection, its scroll or its undo history)."""
        sess = sessions.get(S.song.id) if S.song else None
        info = sess.rhythm if sess else None
        busy_here = bool(S.song) and S.an_busy == S.song.id and S.an_kind == "rhythm"
        cur = LA.text_hash(lyr()) if S.song else ""
        rl = rhythm_lines(sess) if sess else None
        has_result = bool(info and "result" in info)
        fresh = has_result and rhythm_fresh(sess)
        an_state["rt_fresh"] = fresh if has_result else None
        err = (info or {}).get("last_error")
        err_here = bool(err and err.get("text_hash") == cur and err.get("counts_hash") == (RT.counts_hash(rl) if rl else None))
        rt_ring.visible = busy_here
        rt_btn.disabled = (not S.song) or bool(S.an_busy)                  # without syllables the button analyzes them first (one click)
        rt_reask.visible, rt_reask.disabled = has_result, rt_btn.disabled
        rt_err.value = (f"The last rhythm analysis failed: {err['message']}" + (" The earlier result below is kept." if has_result else "")) if err_here else ""
        rt_err.visible = bool(rt_err.value)
        why = ""
        if has_result and not fresh:
            same_text = info["snap"].text_hash == cur
            counts_ok = rl is not None and RT.counts_hash(rl) == info["result"].counts_hash
            if same_text and counts_ok and info["snap"].prompt_version != RT.RHYTHM_PROMPT_VERSION:
                why = "These remarks came from an older prompt. Press Analyze Rhythm to judge again. They do not describe a melody or a BPM."
            else:
                why = ("The syllable counts changed after this analysis (the lyrics did not). " if same_text else "The lyrics changed after this analysis. ") + \
                      "Press Analyze Rhythm to update it. The AI remarks below belong to the older version."
        rt_warn.value, rt_warn.visible = why, bool(why)
        rows, status, note, summary = [], "", "", ""
        if busy_here:
            status = S.an_progress or "Asking the AI…"
        if rl is None:
            rows = [ft.Text("Press Analyze Rhythm: the syllables of these lyrics are analyzed first (one click), then the rhythm. The comparison uses the syllable counts and your manual corrections; "
                            "the AI never counts here, and it does not invent a melody, a groove, or a BPM.", size=12, color=C["amber"])]
            if has_result:
                rows.append(ft.Text("The earlier rhythm result is kept and shown again once the syllables match these lyrics.", size=11, color=C["text3"]))
        else:
            rows = _rt_structure(rl)
            summary = f"{len(rl)} lines · {sum(l.count for l in rl)} units counted from the text · no audio was heard"
            if has_result:
                res: RT.RhythmResult = info["result"]
                rows.append(ft.Text("SINGABILITY SUGGESTIONS  (AI, from the text only)", size=11, weight=ft.FontWeight.BOLD, color=C["text2"]))
                if res.items:
                    by = {}
                    for it in res.items:
                        by.setdefault(it.line, []).append(it)
                    rows += [_rt_item_card(n, res.lines[n - 1], its) for n, its in sorted(by.items())]
                else:
                    rows.append(ft.Text("The AI had no remarks about these lines.", size=11, color=C["text3"]))
                if not busy_here:
                    status = f"{info['snap'].backend.replace('local:', 'Local: ')} · {info.get('at', '')}" + (" · saved result" if (info.get("cached") or info.get("loaded")) else "") + ("" if fresh else " · outdated")
            elif err_here and not busy_here:
                status = "The rhythm analysis failed"
        rt_list.controls = rows
        rt_note.value, rt_note.visible = note, bool(note)
        rt_summary.value, rt_status.value = summary, status
        safe_update(rt_ring, rt_btn, rt_reask, rt_err, rt_warn, rt_note, rt_list, rt_summary, rt_status)

    async def run_rhythm(force: bool = False):
        """Analyze Rhythm: same lyrics + EFFECTIVE counts + backend + model + parameters + prompt gives the saved result (no new AI request); `force` (Refresh) asks the AI again."""
        if not S.song:
            return
        if S.an_busy:
            toast("An analysis is already running"); return
        if S.chat_busy_song:
            toast("The AI is still answering in Chat. Try again in a moment.", C["amber"]); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        song_id, text, backend, host = S.song.id, lyr(), S.backend, S.host
        sess = sessions.get(song_id)
        rl = rhythm_lines(sess)
        if rl is None:                                                  # no syllables for exactly these lyrics: analyze them first, then continue (one click)
            await run_analysis(False)                                   # reuses the saved result when it exists; refuses by itself while another request runs
            if S.an_busy or not S.song or S.song.id != song_id or lyr() != text:
                return                                                  # something else started, or the song / lyrics changed meanwhile: nothing more is started
            sess = sessions.get(song_id)
            rl = rhythm_lines(sess)
            if rl is None:
                toast("The syllables could not be analyzed, so the rhythm was not started. The reason is shown in the Syllables tab.", C["amber"]); return
        ch = RT.counts_hash(rl)
        model, params = an_identity(backend, "rhythm")
        snap = LA.Snapshot(song_id, text, _ui["epoch"], backend, model, f"{params},w={RT.WINDOW_LINES}/{RT.WINDOW_OVERLAP},c={ch[:16]}", RT.RHYTHM_PROMPT_VERSION, kind=RT.KIND)
        at = time.strftime("%H:%M:%S")

        def failed(message: str):
            old = dict(sess.rhythm or {})
            old["last_error"] = {"text_hash": snap.text_hash, "counts_hash": ch, "message": message, "at": at, "backend": backend}      # an earlier good result is kept
            sess.rhythm = old
            render_analysis()
        if not force:
            hit = rt_cache.get(snap.cache_key)
            if hit is not None:
                sess.rhythm = {"snap": snap, "result": hit, "cached": True, "at": at}
                persist_analysis(sess)
                render_analysis()
                (None if rep_busy() else toast("Showing the saved result: no new AI request.")); return
        S.an_busy, S.an_kind, S.an_progress = song_id, "rhythm", "Asking the AI…"
        render_analysis()
        loop = asyncio.get_running_loop()

        ask = spec_ask("songwriter", an_temp("rhythm"), "rhythm")                      # one request of the Songwriter AI's own session

        def progress(k: int, n: int):
            S.an_progress = f"Asking the AI… (part {k} of {n})" if n > 1 else "Asking the AI…"
            loop.call_soon_threadsafe(render_analysis)
        result, error = None, ""
        try:
            result = await loop.run_in_executor(None, lambda: RT.analyze_rhythm(rl, ask, progress=progress))
        except LA.AnalysisError as ex:
            error = str(ex)
            log.warning("rhythm analysis failed: %s | raw=%r", ex, (ex.raw or "")[:6000])
        except Exception as ex:
            error = f"AI error: {ex}"
            log.debug("rhythm analysis AI error: %r", ex)
        finally:
            S.an_busy, S.an_kind, S.an_progress = "", "", ""
        if result is not None:
            log.debug("rhythm raw answers: %r | parsed: %r", result.raw, [(i.line, i.issue, i.severity) for i in result.items])
            rt_cache.put(snap.cache_key, result)                        # only a COMPLETE, validated result is ever cached: a failed window raises before this point
        sess = sessions.get(snap.song_id)
        if sess is None:
            render_analysis(); return                                    # the tab was closed meanwhile
        now_rl = rhythm_lines(sess) if sess.song.lyrics == snap.text else None
        if sess.song.lyrics != snap.text or now_rl is None or RT.counts_hash(now_rl) != ch:     # edited, or the counts were corrected, while waiting: this answer is about the OLD input
            log.debug("rhythm analysis for %s dropped: lyrics or counts changed while waiting", snap.song_id)
            if S.song and S.song.id == snap.song_id:
                toast("The lyrics or the syllable counts changed while the AI was working, so that result was not used. Press Analyze Rhythm again.", C["amber"])
            render_analysis(); return
        if result is None:
            failed(error); return
        sess.rhythm = {"snap": snap, "result": result, "at": at}
        persist_analysis(sess)
        render_analysis()

    # Thesaurus (Phase 5): alternatives for the phrase the user selected, from the AI only. The selection is captured when it is made (Sess.thes_sel), so pressing a button here never
    # depends on where the editor's caret is afterwards. Suggestions are text to read and copy: nothing is applied to the lyrics and set_lyrics() is never called from here. -------
    th_ring = ft.ProgressRing(width=14, height=14, stroke_width=2, visible=False)
    th_status = ft.Text("", size=11, color=C["text3"])
    th_warn = ft.Text("", size=11, color=C["amber"], visible=False)
    th_err = ft.Text("", size=11, color=C["red"], selectable=True, visible=False)
    th_note = ft.Text("", size=11, color=C["text3"], visible=False, selectable=True)
    th_phrase = ft.Text("", size=14, weight=ft.FontWeight.W_600, color=C["text1"], selectable=True)
    th_where = ft.Text("", size=11, color=C["text3"])
    th_preview = ft.Container(ft.Column([th_phrase, th_where], spacing=2), bgcolor=C["field"], border_radius=8, padding=po(10, 8, 10, 8), border=ft.Border.all(1, C["border"]))
    th_list = ft.ListView(expand=True, spacing=6, padding=po(0, 0, 4, 0))
    th_btn = ft.FilledButton("Suggest Alternatives", icon=ft.Icons.AUTO_AWESOME_ROUNDED, height=36, on_click=lambda e: page.run_task(run_thesaurus, False),
                             style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF", shape=ft.RoundedRectangleBorder(radius=8)))
    th_reask = ibtn(ft.Icons.REFRESH_ROUNDED, "Refresh: ask the AI again for the selected phrase (does not use the saved result)", lambda e: page.run_task(run_thesaurus, True), 18)
    th_reask.visible = False
    _TH_HINT = "Select a word or a short phrase in the Lyrics Editor first. It stays here when you click this tab."

    def th_selection(sess):
        """(TH.Selection, None) for the user's captured selection checked against the CURRENT lyrics, or (None, message). Never takes the caret or S.sel and never guesses a range."""
        cap = sess.thes_sel if sess else None
        if not cap:
            return None, _TH_HINT
        text = lyr()
        if text[cap["start"]:cap["end"]] != cap["text"]:
            return None, "The lyrics changed after you selected this phrase. Select it again."
        v = TH.validate_selection(text, cap["start"], cap["end"])
        return (v, None) if isinstance(v, TH.Selection) else (None, v)

    def th_line_count(sess, sel, text=None):
        """The effective syllable count of the WHOLE line holding the phrase (Phase 1-2: manual count > manual segmentation > AI), only when it is current; else None. Never recounted.
        `text` = the lyrics of that song (default: the active song's)."""
        text = lyr() if text is None else text
        info = sess.analysis if sess else None
        if not (info and "result" in info and info["snap"].text_hash == LA.text_hash(text)):
            return None
        first = text.count("\n", 0, sel.start) + 1
        if text.count("\n", 0, sel.end) + 1 != first:
            return None
        effs, _ = LA.effective_lines(info["result"], sess.an_corr)
        return next((e.count for e in effs if e.line.file_line == first), None)

    def th_rhyme(sess, text, sel):
        """(context lines, []) from the CURRENT AI rhyme analysis of `text`: empty when there is none or it is outdated (the AI is never told about a rhyme that the lyrics no longer have)."""
        info = sess.rhyme if sess else None
        fresh = bool(info and "result" in info and info["snap"].text_hash == LA.text_hash(text) and info["snap"].prompt_version == LR.RHYME_PROMPT_VERSION)
        rc = RC.colorize(text, info["result"] if fresh else None, song_lang())           # without a current AI result the line-ending sound groups still exist (found by Python)
        lines, targets = [], []
        for partner, mine in RC.related(rc, sel.start, sel.end):
            ok = mine.verify == "verified" and partner.verify == "verified"
            lines.append(f"“{mine.text}” (line {mine.file_line}) rhymes with “{partner.text}” (line {partner.file_line}) · {mine.kind} · assessment: {mine.verify if mine.verify else 'uncertain'}")
        return lines, targets

    def thes_fresh(sess) -> bool:
        info = sess.thes if sess else None
        if not (info and "result" in info and info["snap"].text_hash == LA.text_hash(lyr())):
            return False
        if info["snap"].prompt_version != TH.THESAURUS_PROMPT_VERSION:
            return False
        r = info["result"]
        v = TH.validate_selection(lyr(), r.start, r.end)
        return isinstance(v, TH.Selection) and v.phrase == r.phrase and TH.build_context(lyr(), v, th_line_count(sess, v), th_rhyme(sess, lyr(), v)[0]).hash == r.ctx_hash

    def th_capture(kind, sel):
        """Called for every selection event of the lyrics field. Only the user's own selections count: a caret (empty selection) clears it. Read-only."""
        if kind != "user" or not S.song:
            return
        sess = sessions.get(S.song.id)
        if sess is None:
            return
        a, b = sel
        sess.thes_sel = {"start": a, "end": b, "text": lyr()[a:b]} if b > a else None
        if rp["tab"] == "an" and an_sub["k"] == "thes":
            refresh_th_preview()

    _hooks["selection"] = th_capture

    def refresh_th_preview():
        sess = sessions.get(S.song.id) if S.song else None
        sel, msg = th_selection(sess) if sess else (None, _TH_HINT)
        if sel is not None:
            th_phrase.value, th_phrase.color = sel.phrase, C["text1"]
            sec = TH.build_context(lyr(), sel).section
            th_where.value = f"line {lyr().count(chr(10), 0, sel.start) + 1}" + (f" · {sec}" if sec else "")
        else:
            th_phrase.value, th_phrase.color, th_where.value = msg, C["text3"], ""
        th_btn.disabled = sel is None or bool(S.an_busy)
        th_reask.disabled = th_btn.disabled
        safe_update(th_phrase, th_where, th_btn, th_reask)

    def th_replace(it):
        """Replace the phrase these suggestions were made for with this one: only while the lyrics are still exactly what the AI saw and the phrase is still at its place. One undo step; nothing else changes."""
        sess = sessions.get(S.song.id) if S.song else None
        info = sess.thes if sess else None
        if not (info and "result" in info):
            return
        if not thes_fresh(sess):
            toast("The lyrics changed after these suggestions were made. Nothing was replaced: ask for new suggestions.", C["amber"]); return
        res = info["result"]
        text = lyr()
        if text[res.start:res.end] != res.phrase:
            toast("The phrase is no longer at its place. Nothing was replaced.", C["amber"]); return
        set_lyrics(text[:res.start] + it.text + text[res.end:], (res.start, res.start + len(it.text)), refocus=False)
        toast(f"Replaced “{res.phrase}” (Undo restores it)", C["green"])

    def _th_card(n: int, it, after=None):
        head = ft.Row([ft.Text(f"{n}.", size=11, color=C["text3"]), ft.Text(it.text, size=14, weight=ft.FontWeight.BOLD, color=C["text1"], selectable=True, expand=True),
                       ft.IconButton(ft.Icons.FIND_REPLACE_ROUNDED, icon_size=20, icon_color="#FFFFFF", bgcolor=C["accent"], width=34, height=34, padding=0, tooltip="Replace the selected phrase with this",
                                     on_click=lambda e, i=it: (th_replace(i), after() if after else None))],
                      spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        raw_diff = (it.difference or "").strip()
        shifted = TH.meaning_shifted(raw_diff)
        shown_diff = raw_diff.split(":", 1)[1].strip() if (shifted or raw_diff.lower().startswith("same:")) else raw_diff
        body = [head, ft.Row([ft.Text(TH.TYPE_TITLES[it.type], size=11, weight=ft.FontWeight.W_600, color=C["accent"]), ft.Text("· AI Suggestion", size=11, color=C["amber"])], spacing=4),
                ft.Text(it.explanation, size=11, color=C["text2"], selectable=True),
                ft.Text(("Meaning changes: " if shifted else "Meaning difference: ") + (shown_diff or raw_diff), size=11,
                        color=C["amber"] if shifted else C["text2"], selectable=True)]
        if it.singability:
            body.append(ft.Text("Singability: " + it.singability, size=11, color=C["text3"], selectable=True))
        if it.rhyme_note:
            body.append(ft.Text("Rhyme: " + it.rhyme_note, size=11, color=C["text3"], selectable=True))
        return ft.Container(ft.Column(body, spacing=2), bgcolor=C["panel2"], border_radius=8, padding=po(8, 6, 8, 6))

    def render_thesaurus():
        """Show the thesaurus of the ACTIVE song (read-only for the lyrics: nothing here touches the lyrics field, its selection, its scroll or its undo history)."""
        sess = sessions.get(S.song.id) if S.song else None
        info = sess.thes if sess else None
        busy_here = bool(S.song) and S.an_busy == S.song.id and S.an_kind == "thes"
        cur = LA.text_hash(lyr()) if S.song else ""
        has_result = bool(info and "result" in info)
        fresh = has_result and thes_fresh(sess)
        an_state["th_fresh"] = fresh if has_result else None
        sel, _msg = th_selection(sess) if sess else (None, "")
        err = (info or {}).get("last_error")
        err_here = bool(err and err.get("text_hash") == cur and sel is not None and err.get("range") == [sel.start, sel.end])
        th_ring.visible = busy_here
        th_reask.visible = has_result
        th_err.value = (f"The last attempt failed: {err['message']}" + (" The earlier suggestions below are kept." if has_result else "")) if err_here else ""
        th_err.visible = bool(th_err.value)
        if has_result and not fresh:
            r = info["result"]
            same_text = info["snap"].text_hash == cur
            v = TH.validate_selection(lyr(), r.start, r.end) if same_text else None
            same_phrase = isinstance(v, TH.Selection) and v.phrase == r.phrase
            if same_phrase and info["snap"].prompt_version != TH.THESAURUS_PROMPT_VERSION:
                th_warn.value = "These suggestions came from an older prompt. Press Suggest Alternatives to ask again."
            else:
                th_warn.value = "The lyrics, the syllable count of this line, or its rhyme analysis changed after these suggestions were made. They belong to the older version."
        else:
            th_warn.value = ""
        th_warn.visible = bool(th_warn.value)
        rows, status = [], ""
        if busy_here:
            status = S.an_progress or "Asking the AI…"
        if has_result:
            res: TH.ThesResult = info["result"]
            where = f"line {res.line}" + (f" · {res.section}" if res.section else "")
            rows.append(ft.Row([ft.Text("SUGGESTIONS", size=11, weight=ft.FontWeight.BOLD, color=C["text2"]), ft.Text("AI Suggestion", size=11, weight=ft.FontWeight.W_600, color=C["amber"])], spacing=8))
            rows.append(ft.Text(f"for “{res.phrase}”  ({where}). Nothing was changed in your lyrics yet.", size=11, color=C["text3"]))
            rows += [_th_card(n, it) for n, it in enumerate(res.items, 1)] if res.items else [ft.Text("No suitable alternatives found", size=12, color=C["text3"])]
            if res.ignored:
                log.debug("thesaurus ignored: %s", res.ignored)
                rows.append(ft.Text(I18N.tr(CG.PARTIAL), size=11, color=C["text3"]))
            if not busy_here:
                status = f"{info['snap'].backend.replace('local:', 'Local: ')} · {info.get('at', '')}" + (" · saved result" if (info.get("cached") or info.get("loaded")) else "") + ("" if fresh else " · outdated")
        elif err_here and not busy_here:
            status = "The request failed"
        if not rows and not busy_here and not th_err.value:
            rows = [ft.Text("Press Suggest Alternatives: the AI reads the phrase in its place in your song and offers words or phrases that could replace it, with what changes in the meaning. "
                            "Nothing changes until you press the replace icon beside a suggestion (Undo restores it).", size=12, color=C["text3"])]
        th_list.controls = rows
        th_status.value = status
        safe_update(th_ring, th_reask, th_err, th_warn, th_list, th_status)
        refresh_th_preview()

    async def run_thesaurus(force: bool = False):
        """Suggest Alternatives for the captured selection: same lyrics + phrase + range + context + backend + model + parameters + prompt gives the saved result (no new request)."""
        if not S.song:
            return
        if S.an_busy:
            toast("An analysis is already running"); return
        if S.chat_busy_song:
            toast("The AI is still answering in Chat. Try again in a moment.", C["amber"]); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        song_id, text, backend, host = S.song.id, lyr(), S.backend, S.host
        sess = sessions.get(song_id)
        sel, msg = th_selection(sess)
        if sel is None:
            toast(msg, C["amber"]); return
        rlines, _rt = th_rhyme(sess, text, sel)
        ctx = TH.build_context(text, sel, th_line_count(sess, sel), rlines)
        model, params = an_identity(backend, "thes")
        snap = LA.Snapshot(song_id, text, _ui["epoch"], backend, model, f"{params},s={song_id},r={sel.start}-{sel.end},p={sel.phrase_hash},x={ctx.hash}", TH.THESAURUS_PROMPT_VERSION, kind=TH.KIND)
        at = time.strftime("%H:%M:%S")

        def failed(message: str):
            old = dict(sess.thes or {})
            old["last_error"] = {"text_hash": snap.text_hash, "range": [sel.start, sel.end], "message": message, "at": at, "backend": backend}      # an earlier good result is kept
            sess.thes = old
            render_analysis()
        if not force:
            hit = th_cache.get(snap.cache_key)
            if hit is not None:
                sess.thes = {"snap": snap, "result": hit, "cached": True, "at": at}
                persist_analysis(sess)
                render_analysis()
                (None if rep_busy() else toast("Showing the saved result: no new AI request.")); return
        S.an_busy, S.an_kind, S.an_progress = song_id, "thes", "Asking the AI…"
        render_analysis()
        loop = asyncio.get_running_loop()

        ask = spec_ask("songwriter", an_temp("thes"), "thes")                      # one request of the Songwriter AI's own session
        result, error = None, ""
        try:
            result = await loop.run_in_executor(None, lambda: TH.suggest(sel, ctx, ask))
        except LA.AnalysisError as ex:
            error = str(ex)
            log.warning("thesaurus failed: %s | raw=%r", ex, (ex.raw or "")[:6000])
        except Exception as ex:
            error = f"AI error: {ex}"
            log.debug("thesaurus AI error: %r", ex)
        finally:
            S.an_busy, S.an_kind, S.an_progress = "", "", ""
        if result is not None:
            log.debug("thesaurus raw answer: %r | parsed: %r | ignored: %r", result.raw, [(i.type, i.text) for i in result.items], result.ignored)
            th_cache.put(snap.cache_key, result)                        # only a COMPLETE, validated result is ever cached
        sess = sessions.get(snap.song_id)
        if sess is None:
            render_analysis(); return                                    # the tab was closed meanwhile
        same = sess.song.lyrics == snap.text
        if same:
            v = TH.validate_selection(sess.song.lyrics, sel.start, sel.end)
            same = isinstance(v, TH.Selection) and v.phrase == sel.phrase and TH.build_context(sess.song.lyrics, v, th_line_count(sess, v, sess.song.lyrics), th_rhyme(sess, sess.song.lyrics, v)[0]).hash == ctx.hash
        if not same:                                                     # edited while waiting: the offsets and the context belong to the OLD lyrics
            log.debug("thesaurus for %s dropped: lyrics or context changed while waiting", snap.song_id)
            if S.song and S.song.id == snap.song_id:
                toast("The lyrics changed while the AI was working, so that result was not used. Select the phrase and try again.", C["amber"])
            render_analysis(); return
        if result is None:
            failed(error); return
        sess.thes = {"snap": snap, "result": result, "at": at}
        persist_analysis(sess)
        render_analysis()

    an_sub = {"k": "syl"}
    an_sub_row = ft.Row(spacing=4, run_spacing=4, wrap=True)
    syl_view =ft.Column([ft.Row([an_btn, an_reask, an_ring, an_status], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                          an_warn, an_err, ft.Container(an_list, expand=True), an_note, an_summary], spacing=6, expand=True)
    rh_view = ft.Column([ft.Row([rh_btn, rh_reask, rh_ring, rh_cancel, rh_status], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                         rh_warn, rh_err, rh_list, rh_note, rh_summary], spacing=6)

    rt_view = ft.Column([ft.Row([rt_btn, rt_reask, rt_ring, rt_status], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                         rt_warn, rt_err, ft.Container(rt_list, expand=True), rt_note, rt_summary], spacing=6, expand=True, visible=False)
    th_view = ft.Column([ft.Text("Selected Phrase", size=11, color=C["text3"]), th_preview, ft.Row([th_btn, th_reask, th_ring, th_status], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                         th_warn, th_err, ft.Container(th_list, expand=True), th_note], spacing=6, expand=True, visible=False)
    AN_SUBS = [("syl", "Syllables"), ("rhyme", "Rhymes"), ("rhythm", "Rhythm"), ("thes", "Thesaurus")]

    def pick_an_sub(k: str):
        an_sub["k"] = k
        an_sub_row.controls = seg_buttons(AN_SUBS, k, pick_an_sub)
        syl_view.visible, rh_view.visible, rt_view.visible, th_view.visible = k == "syl", k == "rhyme", k == "rhythm", k == "thes"
        safe_update(an_sub_row, syl_view, rh_view, rt_view, th_view)
        if k == "thes":
            refresh_th_preview()
    an_sub_row.controls = seg_buttons(AN_SUBS, "syl", pick_an_sub)
    analysis_view = ft.Column([an_sub_row, syl_view, rt_view, th_view], spacing=6, expand=True)          # the rhyme report is a page of the Report tab now (rh_view)

    # ═══ AI REVIEW: the main way to improve the lyrics. One request for the selection / the current section / the whole song; the AI writes at most 3 suggestions; the user previews, applies or
    # ignores each one, then reviews again. The AI judges; this code only builds the request, checks the structure of the answer, finds the exact place of a suggestion when it is applied
    # (set_lyrics: one Undo step) and remembers the decisions so the next review does not repeat itself. ─────────────────────────────────────────────────────────────────────────
    rv_cache = LA.ResultCache()
    RV_FOCUS = {"meaning": "Meaning", "emotion": "Emotion", "rhyme": "Rhyme", "flow": "Flow", "phrasing": "Phrasing", "word": "Word choice", "structure": "Structure", "other": "Other"}
    review_title = ft.Text("", size=12, weight=ft.FontWeight.BOLD, color=C["text1"], expand=True)
    review_ring = ft.ProgressRing(width=14, height=14, stroke_width=2, visible=False)
    review_summary = ft.Text("", size=11, color=C["text2"], selectable=True)
    review_warn = ft.Text("", size=11, color=C["amber"], visible=False)
    review_err = ft.Text("", size=11, color=C["red"], selectable=True, visible=False)
    review_list = ft.Column([], spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
    review_again = ft.TextButton("Review Again", icon=ft.Icons.REFRESH_ROUNDED, on_click=lambda e: review_again_click(), style=ft.ButtonStyle(color=C["accent"], padding=po(6, 0, 6, 0)))
    review_close = ibtn(ft.Icons.CLOSE_ROUNDED, "Close the review", lambda e: close_review(), 16, w=28)          # for a scoped AI Review only: a report page has its own button and keeps its report
    review_box = ft.Container(ft.Column([ft.Row([review_ring, review_title, review_again, review_close], spacing=4,
                                                 vertical_alignment=ft.CrossAxisAlignment.CENTER), review_warn, review_err, review_summary, review_list], spacing=6, expand=True),
                              bgcolor=C["panel2"], border_radius=10, padding=10, expand=True)

    def review_state():
        sess = sessions.get(S.song.id) if S.song else None
        return sess, (sess.review if sess else None)

    def close_review():
        sess, _rv = review_state()
        if sess is not None:
            sess.review, sess.report_on = None, False
        render_review()

    def review_again_click():
        sess, rv = review_state()
        if sess is not None and sess.report_on:
            start_report(report_view["k"]); return
        res = rv.get("result") if rv else None
        start_review("all" if (res and res.scope.mode == "all") else "auto")

    def _rv_lyric_box(text: str, color: str, selectable: bool = True):
        return ft.Container(ft.Text(spans=[ft.TextSpan(text)], size=12, color=color, selectable=selectable), bgcolor=C["field"], border_radius=6, padding=po(8, 5, 8, 5))

    def _rv_card(n: int, item, res, same_sheet: bool):
        loc = SE.locate_text(item, lyr()) if (item.status == "open" and same_sheet) else None
        can = isinstance(loc, tuple)
        where = f"Line {item.first}" if item.first == item.last else f"Lines {item.first}-{item.last}"
        body = [ft.Row([ft.Text(f"{n}. {RV_FOCUS.get(item.focus, 'Other')}", size=12, weight=ft.FontWeight.W_600, color=C["accent"]), ft.Text(where, size=11, color=C["text3"]),
                        ft.Text(f"AI confidence: {item.confidence}", size=10, color=C["text3"])], spacing=8, wrap=True),
                _rv_lyric_box(item.original, C["text2"]), ft.Icon(ft.Icons.ARROW_DOWNWARD_ROUNDED, size=14, color=C["text3"]), _rv_lyric_box(item.suggestion, C["green"]),
                ft.Text(item.reason, size=11, color=C["text2"], selectable=True)]
        if item.status == "applied":
            body.append(ft.Text("Applied. Undo in the editor restores the earlier lyrics.", size=11, color=C["green"]))
        elif item.status == "ignored":
            body.append(ft.Text("Ignored", size=11, color=C["text3"]))
        else:
            if not same_sheet:
                body.append(ft.Text("This review belongs to another sheet. Open that sheet to apply it.", size=11, color=C["amber"]))
            elif isinstance(loc, str):
                body.append(ft.Text(loc, size=11, color=C["amber"]))
            body.append(ft.Row([ft.OutlinedButton("Preview", on_click=lambda e, i=item: rv_preview(i), disabled=not can),
                                ft.FilledButton("Apply", on_click=lambda e, i=item: rv_apply(i), disabled=not can, style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF")),
                                ft.TextButton("Copy", on_click=lambda e, i=item: page.run_task(copy_text, i.suggestion, "Suggestion")),
                                ft.TextButton("Ignore", on_click=lambda e, i=item: rv_ignore(i))], spacing=6, wrap=True))
        return ft.Container(ft.Column(body, spacing=5), bgcolor=C["panel"], border_radius=8, padding=po(10, 8, 10, 8))

    # ═══ SONG ANALYSIS REPORT: Analyze Song asks two specialists (Songwriter AI, Song Architect) and puts their reports together as ONE report. ══════════════════
    # Each specialist works in its own session with the same snapshot of the song and answers with a structured report (specialist_engine.parse_report): the Songwriter AI one report on the language, the
    # meaning and the sound of the lyrics (it does not run the syllable / rhyme / rhythm engines; those stay tools of their own, and their results are shown in its section when they are current).
    # The reports of the former specialists stay readable in an archive section. A specialist that fails does not remove the other, and can be retried alone. Python only orders, merges and checks places: it judges nothing about the writing. The report is only made when the user asks; typing never calls an AI.
    rep = {"running": False, "step": "", "spec": ""}
    rep_open = {"ov": True, "sw": False, "archive": False, "hk": False, "pi": True, "syl_all": False, "rh_pairs": False, "rt_len": False}
    REP_PARTS = tuple((sp_id, SP.name_of(sp_id)) for sp_id in SP.ANALYZE_ORDER)
    report_view = {"k": "songwriter"}                                     # the report of the Report tab that is shown (and run by its button): songwriter | music_producer

    def _parts() -> tuple:
        """The specialist(s) of the report that is open: each report is its own panel and runs on its own."""
        return tuple(p for p in REP_PARTS if p[0] == report_view["k"]) or REP_PARTS
    SPEC_SECTION = {"songwriter": ("sw", "Lyrics, Meaning & Flow"), "music_producer": ("hk", "Structure, Cues & Music Production")}
    ARCH_GROUPS = (("Existing Section Review", ("section_review",)), ("Cue Recommendations", ("cue",)), ("Hook / Chorus", ("hook", "chorus", "repetition")), ("Arrangement & Sound", ("arrangement",)), ("Suggested Changes", ()))      # () = every other kind

    def rep_busy() -> bool:
        return bool(rep["running"])

    def eng_state(sess, part: str) -> tuple:
        """(state, message) of one prosody engine (syl | rhyme | rhythm) for the CURRENT lyrics: ok | running | error | outdated | missing."""
        cur = LA.text_hash(lyr()) if S.song else ""
        info, kind, ver = {"syl": (sess.analysis, "syl", LA.PROMPT_VERSION), "rhyme": (sess.rhyme, "rhyme", LR.RHYME_PROMPT_VERSION),
                           "rhythm": (sess.rhythm, "rhythm", RT.RHYTHM_PROMPT_VERSION)}[part]
        if S.song and S.an_busy == S.song.id and S.an_kind == kind:
            return "running", ""
        info = info or {}
        has = "result" in info
        fresh = has and (rhythm_fresh(sess) if part == "rhythm" else (info["snap"].text_hash == cur and info["snap"].prompt_version == ver))
        if fresh:
            return "ok", ""
        err = info.get("last_error")
        if err and err.get("text_hash") == cur:
            return "error", str(err.get("message", ""))
        return ("outdated" if has else "missing"), ""

    def rep_state(sess, spec: str) -> tuple:
        """(state, message) of one specialist's part of the report: ok | running | error | outdated | missing."""
        cur = LA.text_hash(lyr()) if S.song else ""
        if rep["running"] and rep["spec"] == spec:
            return "running", ""
        r = sess.specreports.get(spec)
        if r is None:
            return "missing", ""
        if r.status == "error":
            return ("error", r.error) if r.lyrics_hash == cur else ("outdated", "")
        try:
            now_ph = SP.load(spec).hash
        except Exception:
            now_ph = ""
        old_prompt = bool(r.prompt_hash and now_ph and r.prompt_hash != now_ph)          # made with another prompt (the role changed or the file was edited)
        return ("ok" if (r.lyrics_hash == cur and not old_prompt) else "outdated"), ""

    def _rep_tile(key: str, title: str, controls: list, subtitle: str = ""):
        def changed(e):
            try:
                rep_open[key] = bool(e.control.expanded)
            except Exception:
                pass
        return ft.ExpansionTile(title=ft.Text(title, size=13, weight=ft.FontWeight.BOLD, color=C["text1"]), subtitle=ft.Text(subtitle, size=11, color=C["text3"]) if subtitle else None,
                                controls=controls, expanded=rep_open.get(key, False), on_change=changed, tile_padding=po(4, 0, 4, 0), controls_padding=po(4, 4, 4, 8), maintain_state=True,
                                expanded_cross_axis_alignment=ft.CrossAxisAlignment.START)           # the report reads left-aligned (the default centres every row)

    def _rep_line_row(e, fresh: bool):
        badge = ft.Container(ft.Text(str(e.count), size=11, weight=ft.FontWeight.BOLD, color="#FFFFFF"), bgcolor=C["amber"] if e.manual else C["accent"], border_radius=6, padding=po(6, 1, 6, 1))
        head = [ft.Text(str(e.line.num), size=10, color=C["text3"], width=20), ft.Text(e.line.text, size=11, color=C["text1"], expand=True, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS), badge]
        if fresh:
            head.append(ft.IconButton(ft.Icons.EDIT_OUTLINED, icon_size=14, width=24, height=24, padding=0, tooltip="Edit Syllables", icon_color=C["text2"], on_click=lambda ev, x=e: open_edit_syllables(x)))
        body = [ft.Row(head, spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER)]
        extra = " · ".join(x for x in (e.note, e.warn, "your correction" if e.manual else "") if x)
        if extra:
            body.append(ft.Text(extra, size=10, color=C["amber"]))
        row = ft.Container(ft.Column(body, spacing=0), padding=po(2, 2, 2, 2), border_radius=6)
        if fresh:
            row.ink, row.on_click, row.tooltip = True, (lambda ev, n=e.line.file_line: goto_file_line(n)), f"Go to line {e.line.file_line} in the editor"
        return row

    def _rep_syllables(sess) -> list:
        effs, _inactive = LA.effective_lines(sess.analysis["result"], sess.an_corr)
        flagged = [x for x in effs if x.note or x.warn or x.manual]
        out = [ft.Text(f"{len(effs)} lines · {LA.effective_total(effs)} spoken units · not a sung-note count. Counts are information for you, not a verdict: lines may differ in length on purpose.", size=11, color=C["text2"])]
        if flagged:
            out.append(ft.Text("Lines to check", size=11, weight=ft.FontWeight.BOLD, color=C["text2"]))
            out += [_rep_line_row(x, True) for x in flagged]
        out.append(_rep_tile("syl_all", f"All lines ({len(effs)})", [_rep_line_row(x, True) for x in effs]))
        return out

    def _rep_rhymes(sess) -> list:
        res: LR.RhymeResult = sess.rhyme["result"]
        out = []
        prev = _rc_preview(res)
        if prev is not None:
            out.append(prev)
        out.append(ft.Text(f"{sum(1 for p in res.pairs if p.verify == 'llm_exact')} LLM-assessed exact · {sum(1 for p in res.pairs if p.verify == 'llm_near')} LLM-assessed near · "
                           f"{sum(1 for p in res.pairs if p.verify == 'uncertain')} uncertain · the AI judged the sound; nothing independent verified it", size=11, color=C["text2"]))
        cards = [_rh_card(p, res.lines) for p in res.pairs] + [_rh_card(p, res.lines) for p in res.rejected]
        if cards:
            out.append(_rep_tile("rh_pairs", f"Pairs ({len(cards)})", cards))
        if res.ignored:
            log.debug("rhyme report ignored: %s", res.ignored)
            out.append(ft.Text(I18N.tr(CG.PARTIAL), size=10, color=C["text3"]))
        return out

    def _rep_rhythm(sess) -> list:
        rl = rhythm_lines(sess)
        res: RT.RhythmResult = sess.rhythm["result"]
        out = []
        if res.items:
            out.append(ft.Text(f"{len(res.items)} flow remark(s) from the text only (no melody, BPM or audio was heard). The ones with a suggestion are in the Priority Improvements below.", size=11, color=C["text2"]))
        else:
            out.append(ft.Text("The AI had no remarks about the rhythm and flow.", size=11, color=C["green"]))
        if rl:
            out.append(_rep_tile("rt_len", "Line lengths", _rt_structure(rl)))
        return out

    # ── findings: the cards of the Songwriter AI and the Song Architect ────────────────────────────────────────
    PRIORITY_COL = {"high": C["red"], "medium": C["amber"], "low": C["text3"]}

    def _fd_item(f, opt: "str | None" = None) -> "RV.Item":
        return RV.Item(f.id, f.kind, f.first, f.last, f.evidence, opt if opt is not None else f.suggestion, f.reason)

    # The report cards are drawn once per report; an Apply / Ignore / Undo / filter only redraws the cards it concerns (fd_view["reg"]: finding key -> [(card, finding)]).
    fd_view = {"btns": [], "filter": "all", "pick": {}, "reg": {}, "seen": {}, "bar": None, "busy": False, "where": None, "due": 0.0, "task": False, "hl": None}

    def _fd_key(f) -> str:
        return f"{f.spec}:{f.id}"

    def _fd_state(f, sess) -> str:
        """open | applied | ignored. 'Applied' is checked against the lyrics as they are NOW: Undo that brought the original text back makes it open again, Redo to exactly the applied lyrics makes it applied again."""
        key = _fd_key(f)
        st, cur = sess.fd_status.get(key, "open"), SE.text_hash_of(lyr())
        if st.startswith("applied:"):
            if st != "applied:" + cur and not isinstance(SE.locate_text(_fd_item(f), lyr()), str) and f.suggestion.strip() not in lyr():
                sess.fd_status.pop(key, None)                     # Undo brought the original text back: the suggestion can be applied again
                sess.fd_undone[key] = st
                return "open"
            return "applied"
        if st == "open" and sess.fd_undone.get(key) == "applied:" + cur:
            sess.fd_status[key] = sess.fd_undone.pop(key)         # Redo: the very lyrics it was applied to are back
            return "applied"
        return "ignored" if st == "ignored" else "open"

    def _fd_all(sess) -> list:
        """Every finding of the report ONCE, in priority order: the same finding found by two specialists is one (aggregate merges real duplicates)."""
        return SE.aggregate([r for r in sess.specreports.values()], limit=10 ** 6)

    def _fd_twins(f, sess) -> list:
        """The same finding of another specialist (aggregate shows it once; here it is found in its own section): applying or ignoring one marks the other, so it is never applied twice."""
        def n(t):
            return " ".join((t or "").lower().split())
        out = []
        for r in sess.specreports.values():
            if r.status != "ok":
                continue
            for g in r.findings:
                if (g.spec, g.id) != (f.spec, f.id) and f.first and (g.first, g.last) == (f.first, f.last) and ((n(f.suggestion) and n(f.suggestion) == n(g.suggestion)) or (n(f.finding) and n(f.finding) == n(g.finding))):
                    out.append(g)
        return out

    def _fd_match(f, sess) -> bool:
        return fd_view["filter"] == "all" or _fd_state(f, sess) == fd_view["filter"]

    def _fd_bar_controls(sess) -> list:
        cnt = {"all": 0, "open": 0, "applied": 0, "ignored": 0}
        for f in _fd_all(sess):
            cnt["all"] += 1
            cnt[_fd_state(f, sess)] += 1
        out = []
        for k, label in (("all", "All"), ("open", "Pending"), ("applied", "Applied"), ("ignored", "Ignored")):
            on = fd_view["filter"] == k
            col = "#FFFFFF" if on else C["text2"]
            out.append(ft.Container(ft.Row([ft.Text(label, size=11, weight=ft.FontWeight.W_600, color=col), ft.Text(str(cnt[k]), size=11, weight=ft.FontWeight.BOLD, color=col)], spacing=4, tight=True),
                                    padding=po(10, 3, 10, 3), border_radius=12, bgcolor=C["accent"] if on else C["field"], ink=True, on_click=lambda e, k=k: fd_set_filter(k)))
        out.append(ft.TextButton("Review all suggestions", icon=ft.Icons.DIFFERENCE_OUTLINED, on_click=lambda e: open_review_diff(None), style=ft.ButtonStyle(color=C["accent"], padding=po(8, 0, 8, 0))))
        return out

    def fd_bar_refresh(sess):
        if fd_view["bar"] is not None:
            fd_view["bar"].controls = _fd_bar_controls(sess)
            safe_update(fd_view["bar"])

    def fd_set_filter(k: str):
        sess, _rv = review_state()
        if sess is None:
            return
        fd_view["filter"] = k
        shown = []
        try:
            for ents in fd_view["reg"].values():
                for cont, g in ents:
                    v = _fd_match(g, sess)
                    if cont.visible != v:
                        cont.visible = v
                        shown.append(cont)
        except RuntimeError as ex:                                # a card of an earlier drawing (Flet froze it when the report was replaced): the whole report is drawn again with the new filter
            dbg_once("report filter", ex)
            render_review()
            return
        fd_bar_refresh(sess)
        safe_update(*shown)

    def _fd_redraw(key: str, sess):
        for cont, g in fd_view["reg"].get(key, []):
            try:
                cont.content = _fd_row_col(g, sess) if cont.data == "row" else _fd_col(g, sess)
                cont.visible = _fd_match(g, sess)
                if cont.data != "row":
                    cont.border = ft.Border.all(1, "#00000000")
            except RuntimeError as ex:                            # a frozen card of an earlier drawing: draw the report again instead of failing
                dbg_once("report card redraw", ex)
                render_review()
                return
            safe_update(cont)
        ents = fd_view["reg"].get(key)
        if ents:
            fd_view["seen"][key] = _fd_state(ents[0][1], sess)

    def _cc_after():
        if fd_view.get("after") is not None:
            fd_view["after"]()

    def fd_refresh(f, sess):
        """Only the cards of this finding (and of its twin in another specialist's section) and the filter counts are redrawn; the rest of the report and its scroll position stay as they are."""
        for g in [f] + _fd_twins(f, sess):
            _fd_redraw(_fd_key(g), sess)
        fd_bar_refresh(sess)
        for fn in list(fd_view.get("btns", [])):
            fn()
        _cc_after()

    def fd_pick(f, n: int):
        sess, _rv = review_state()
        fd_view["pick"][_fd_key(f)] = n
        if sess is not None:
            _fd_redraw(_fd_key(f), sess)

    def fd_sync():
        """Undo / Redo / typing in the editor can change what is 'applied': after the editing pauses, the cards whose state changed are redrawn (none of the others)."""
        sess, _rv = review_state()
        if sess is None or not sess.report_on or not fd_view["reg"] or not S.song or fd_view["where"] != (S.song.id, S.song.book.active_id):
            return
        changed = False
        for key, ents in list(fd_view["reg"].items()):
            if _fd_state(ents[0][1], sess) != fd_view["seen"].get(key):
                _fd_redraw(key, sess)
                changed = True
        if changed:
            fd_bar_refresh(sess)
            for fn in list(fd_view.get("btns", [])):
                fn()
            _cc_after()

    async def fd_later():
        try:
            while time.time() < fd_view["due"]:
                await asyncio.sleep(0.1)
            fd_sync()
        finally:
            fd_view["task"] = False

    def fd_tick():
        if not fd_view["reg"]:
            return
        fd_view["due"] = time.time() + 0.5
        if not fd_view["task"]:
            fd_view["task"] = True
            page.run_task(fd_later)
    dr_hooks.append(fd_tick)

    def goto_finding(f):
        """Select the finding's lyrics in the editor. They are found by their quoted text in the CURRENT lyrics (the line numbers may have moved); a finding with no quote only when the lyrics are the ones the report was made for. Nothing is changed."""
        sess, _rv = review_state()
        if not S.song or sess is None:
            return
        text = lyr()
        if f.evidence and f.first:
            loc = SE.locate_text(_fd_item(f), text)
            if isinstance(loc, tuple):
                S.sel = loc
                show_lyrics_to_ui(select=S.sel, focus=True)
                return
        elif f.first and sess.report_hash == LA.text_hash(text):
            goto_file_line(f.first)
            return
        toast("This report does not match the current lyrics (the quoted text was changed). Analyze again.", C["amber"])

    def _fd_col(f, sess):
        key = _fd_key(f)
        status = _fd_state(f, sess)
        item = _fd_item(f)
        loc = SE.locate_text(item, lyr()) if (f.suggestion and f.evidence and f.first and status == "open") else None      # found by its quoted text, so it can still be applied after another suggestion moved the lines
        can = isinstance(loc, tuple)
        where = (f"Line {f.first}" if f.first == f.last else f"Lines {f.first}-{f.last}") if f.first else "Whole song"
        where_ctl = (ft.Container(ft.Text(where, size=11, weight=ft.FontWeight.W_600, color=C["accent"]), on_click=lambda e, g=f: goto_finding(g), ink=True, border_radius=4, tooltip="Go to this line in the editor")
                     if f.first else ft.Text(where, size=11, color=C["text3"]))
        head = [ft.Text(SP.name_of(f.spec), size=11, weight=ft.FontWeight.W_600, color=C["accent"]), where_ctl, ft.Text(f.priority, size=10, weight=ft.FontWeight.BOLD, color=PRIORITY_COL.get(f.priority, C["text3"]))]
        if f.also_by:
            head.append(ft.Text("also: " + ", ".join(SP.name_of(x) for x in f.also_by), size=10, color=C["text3"]))
        body = [ft.Row(head, spacing=8, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER), ref_text(f.finding, size=12, color=C["text1"], selectable=True)]
        opts = f.options if len(f.options) > 1 else []
        pick = min(fd_view["pick"].get(key, 0), len(opts) - 1) if opts else 0
        if f.evidence:
            body += [ft.Text("Original", size=10, weight=ft.FontWeight.BOLD, color=C["text3"]), _rv_lyric_box(f.evidence, C["text2"])]
        if f.suggestion and not opts:
            body += [ft.Text("Suggested", size=10, weight=ft.FontWeight.BOLD, color=C["text3"]), _rv_lyric_box(f.suggestion, C["green"])]
        for n_, o_ in enumerate(opts):                            # several wordings: choose one (the frame), then Apply
            on_ = status == "open" and n_ == pick
            body.append(ft.Container(ft.Column([ft.Text(f"Option {n_ + 1}", size=10, weight=ft.FontWeight.BOLD, color=C["accent"] if on_ else C["text3"]), _rv_lyric_box(o_, C["green"], selectable=False)], spacing=2),
                                     padding=po(4, 3, 4, 3), border_radius=8, border=ft.Border.all(2, C["accent"] if on_ else "#00000000"), ink=status == "open",
                                     on_click=(lambda e, g=f, n=n_: fd_pick(g, n)) if status == "open" else None))
        if f.reason:
            body.append(ref_text(f.reason, size=11, color=C["text2"], selectable=True))
        for lab_, val_ in (("Meaning", f.meaning), ("Rhyme", f.rhyme_effect), ("Syllables", f.syllables)):       # the AI's own check of its suggestion; a field it could not judge is not shown
            if val_ and (f.suggestion or opts):
                body.append(ft.Text(f"{lab_}: {val_}", size=11, color=C["text2"], selectable=True))
        if f.tradeoff:
            body.append(ref_text(f.tradeoff, "Trade-off: ", size=11, color=C["amber"], selectable=True))
        chosen = opts[pick] if opts else f.suggestion
        opt = chosen if opts else None
        if status == "applied":
            body.append(ft.Text("Applied. Undo in the editor restores the earlier lyrics.", size=11, color=C["green"]))
        elif status == "ignored":
            body.append(ft.Text("Ignored", size=11, color=C["text3"]))
        elif f.suggestion and f.evidence and f.first and not can:
            body.append(ft.Text(loc if isinstance(loc, str) else "The quoted text is not in the lyrics any more, so this suggestion cannot be applied. Analyze again.", size=11, color=C["amber"]))
        has_sug = bool(f.suggestion and f.evidence and f.first)
        more = []
        if status == "open" and has_sug:
            more.append(ft.PopupMenuItem(content=ft.Text("Review in the full lyrics"), disabled=not can, on_click=lambda e, g=f: open_review_diff(_fd_key(g))))
        more.append(ft.PopupMenuItem(content=ft.Text("Ask AI about this finding"), on_click=lambda e, g=f: ask_specialist(g)))
        if f.suggestion:
            more.append(ft.PopupMenuItem(content=ft.Text("Copy suggestion"), on_click=lambda e, t=chosen: page.run_task(copy_text, t, "Suggestion")))
        if status == "open":
            more.append(ft.PopupMenuItem(content=ft.Text("Ignore"), on_click=lambda e, g=f: fd_ignore(g)))
        elif status == "ignored":
            more.append(ft.PopupMenuItem(content=ft.Text("Restore to pending"), on_click=lambda e, g=f: fd_restore(g)))
        btns = []
        if f.first:
            btns.append(ft.OutlinedButton("Go to line", on_click=lambda e, g=f: goto_finding(g)))
        if status == "open" and has_sug:
            btns.append(ft.FilledButton("Apply", on_click=lambda e, g=f: open_review_diff(_fd_key(g)), disabled=not can, style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF")))
        btns.append(ft.PopupMenuButton(icon=ft.Icons.MORE_HORIZ_ROUNDED, icon_color=C["text2"], icon_size=18, tooltip="More", items=more))
        body.append(ft.Row(btns, spacing=6, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        return ft.Column(body, spacing=5)

    def _fd_applicable(f) -> bool:
        return bool(f.suggestion and f.evidence and f.first and len(f.options) <= 1)

    def _fd_row_col(f, sess):
        """A suggestion as ONE plain line (the same look as the AI Music Director's cue list): what it is about and what it would become. Deciding is done in the review of all of them."""
        status = _fd_state(f, sess)
        where = (f"Line {f.first}" if f.first == f.last else f"Lines {f.first}-{f.last}") if f.first else ""
        txt = ft.Text((where + ": " if where else "") + f.finding + (f"  →  {f.suggestion}" if f.suggestion else ""), size=11, color=C["text2"], expand=True, selectable=True)
        side = (ft.Text("Applied", size=10, color=C["green"], weight=ft.FontWeight.W_600) if status == "applied" else ft.Text("Ignored", size=10, color=C["text3"]) if status == "ignored" else ft.Container(width=1))
        return ft.Row([txt, side], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _fd_review_btn(fs: list, sess):
        """The one button of a list of such lines: 'Review and apply (N)…' (N = still pending); it opens the review of all suggestions."""
        def label():
            n = sum(1 for g in fs if _fd_state(g, sess) == "open")
            return n, f"Review and apply ({n})…"
        n0, t0 = label()
        btn = ft.OutlinedButton(t0, on_click=lambda e: open_review_diff(None), style=ft.ButtonStyle(padding=po(12, 0, 12, 0)))
        box = ft.Container(ft.Row([ft.Container(expand=True), btn]), visible=n0 > 0)

        def refresh():
            n, t = label()
            btn.content, box.visible = t, n > 0
            safe_update(box)
        fd_view["btns"].append(refresh)
        return box

    def _fd_card(f, sess, compact: bool = False):
        key = _fd_key(f)
        if compact:
            cont = ft.Container(_fd_row_col(f, sess), padding=po(2, 2, 2, 2), data="row")
        else:
            cont = ft.Container(_fd_col(f, sess), bgcolor=C["panel"], border_radius=8, padding=po(10, 8, 10, 8), border=ft.Border.all(1, "#00000000"))
        cont.visible = _fd_match(f, sess)
        ents = fd_view["reg"].setdefault(key, [])
        if not ents:
            cont.key = ft.ScrollKey("fd:" + key)                                # the first card of a finding is where the report scrolls to
            fd_view["seen"][key] = _fd_state(f, sess)
        ents.append((cont, f))
        return cont

    def _fd_next(f, sess):
        """The next finding after f (in the priority order, around the end) that is still pending and can be applied now. Nothing is applied for the user."""
        order = _fd_all(sess)
        keys = [_fd_key(g) for g in order]
        i = keys.index(_fd_key(f)) if _fd_key(f) in keys else -1
        for g in order[i + 1:] + order[:i + 1]:
            if _fd_state(g, sess) == "open" and g.suggestion and g.evidence and g.first and isinstance(SE.locate_text(_fd_item(g), lyr()), tuple):
                return g
        return None

    async def fd_scroll(key: str):
        try:
            await asyncio.sleep(0.25)                          # the redrawn cards have reached the page
            await review_list.scroll_to(scroll_key="fd:" + key, duration=250)
        except Exception as ex:
            log.debug("report scroll_to failed: %r", ex)

    def _dr_cue_edit(f):
        """(new lyrics, selection) for one Music Director cue, or a sentence for the user.

        A section cue replaces only that [section] line. A line vocal cue inserts one [cue] line immediately before the lyric. The lyric characters in the span stay.
        """
        text = lyr()
        loc = SE.locate_text(_fd_item(f), text)
        if isinstance(loc, str):
            return loc
        a, b = loc
        found = text[a:b]
        if f.kind == "line_vocal":
            parts = (f.suggestion or "").split("\n")
            cue = parts[0].strip() if parts else ""
            if len(parts) != 2 or found != f.evidence or parts[1] != f.evidence or not re.fullmatch(r"\[[^\]\n]+\]", cue) or not CL.line_vocal(cue[1:-1]):
                return "This cue could not be applied: the lyric line is not what it was."
            inserted = cue + "\n" + found
            return text[:a] + inserted + text[b:], (a, a + len(inserted))
        if found.strip() != f.evidence.strip() or not re.fullmatch(r"\[[^\]\n]+\]", (f.suggestion or "").strip()):
            return "This cue could not be applied: the section line is not what it was."
        return text[:a] + f.suggestion.strip() + text[b:], (a, a + len(f.suggestion.strip()))

    def fd_apply(f, opt: "str | None" = None) -> bool:
        """Apply ONE suggestion of a report (or one of its wordings): where its quoted text is NOW in the lyrics (the line numbers may have moved after an earlier Apply), on the open sheet, as one Undo step.
        True when it was applied; then the next pending suggestion is shown. False (and the reason shown) when it was not."""
        sess, _rv = review_state()
        if sess is None or not S.song or fd_view["busy"]:
            return False
        if f.spec == "music_director":
            if not any(g.id == f.id for g in dr_cue_findings()):
                toast("This cue belongs to another song, sheet or analysis. Nothing was changed.", C["amber"]); return False
        elif not any(g.id == f.id and g.spec == f.spec for r in sess.specreports.values() for g in r.findings):
            toast("This suggestion belongs to another song or sheet. Nothing was changed.", C["amber"]); return False
        key = _fd_key(f)
        if _fd_state(f, sess) != "open":
            return False
        fd_view["busy"] = True                                     # a second quick click while this one runs is not applied again
        try:
            got = _dr_cue_edit(f) if f.spec == "music_director" else SE.apply_change(_fd_item(f, opt), lyr(), f.spec)
            if isinstance(got, str):
                toast(got, C["amber"]); fd_refresh(f, sess); return False
            new_text, sel = got
            set_lyrics(new_text, sel, refocus=False)
            mark = "applied:" + SE.text_hash_of(new_text)
            for g in [f] + _fd_twins(f, sess):
                sess.fd_status[_fd_key(g)] = mark
                sess.fd_undone.pop(_fd_key(g), None)
            sess.review_dec.append(("applied", f.evidence, opt if opt is not None else f.suggestion))
            TRC.log_event("apply", specialist=f.spec, finding=f.id, original=f.evidence, applied_text=opt if opt is not None else f.suggestion, line_hint=f.first)
            if f.spec == "music_director":
                dr_render()                                        # the cue list of the AI Music Director shows it as applied
            if not review_warn.visible and sess.report_hash and sess.report_hash != LA.text_hash(lyr()):     # the first change makes the report 'outdated': the banner (and the rhyme colours) need the full redraw once
                render_review()
            else:
                fd_refresh(f, sess)
            nxt = _fd_next(f, sess)
            if nxt is not None:
                toast(f"Applied (Undo restores the previous lyrics). Next suggestion: line {nxt.first}", C["green"])
                page.run_task(fd_scroll, _fd_key(nxt))
            else:
                toast("Applied (Undo restores the previous lyrics). No more pending suggestions.", C["green"])
            return True
        finally:
            fd_view["busy"] = False

    def fd_ignore(f):
        sess, _rv = review_state()
        if sess is not None:
            for g in [f] + _fd_twins(f, sess):
                sess.fd_status[_fd_key(g)] = "ignored"
            if f.suggestion:
                sess.review_dec.append(("ignored", f.evidence, f.suggestion))
            fd_refresh(f, sess)

    def fd_restore(f):
        sess, _rv = review_state()
        if sess is not None:
            for g in [f] + _fd_twins(f, sess):
                sess.fd_status.pop(_fd_key(g), None)
            fd_refresh(f, sess)

    rdv = {"body": None, "title": None, "focus": None}

    RD_RED, RD_GREEN = "#B91C1C", "#15803D"

    def _rd_chips(f) -> list:
        chips = [ft.Container(ft.Text(SP.name_of(x), size=9, weight=ft.FontWeight.W_600, color="#FFFFFF"), bgcolor=CC_SPEC_COL.get(x, C["text3"]), border_radius=6, padding=po(5, 0, 5, 1))
                 for x in [f.spec] + list(f.also_by)]
        if f.conflicts:
            chips.append(ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=13, color=C["amber"], tooltip="Another specialist suggests something different for these lines. Accepting one removes the other."))
        return chips

    def _rd_btn(label: str, icon, on_click, filled: bool):
        col = RD_GREEN if filled else C["paper_dim"]
        return ft.Container(ft.Row([ft.Icon(icon, size=14, color="#FFFFFF" if filled else RD_RED), ft.Text(label, size=11, weight=ft.FontWeight.W_600, color="#FFFFFF" if filled else C["ink"])], spacing=3, tight=True),
                            bgcolor=RD_GREEN if filled else None, border=None if filled else ft.Border.all(1, col), border_radius=6, padding=po(8, 3, 8, 3), ink=True, on_click=on_click)

    def _rd_hunk(f, shown: str, opt, n0: int, n1: int, text_lines: list, with_old: bool):
        parts = []
        if with_old:
            old = [ft.Row([ft.Text(str(n), size=10, color=C["paper_dim"], width=22), ft.Text(text_lines[n - 1] if 0 < n <= len(text_lines) else "", size=13, color=RD_RED, expand=True)], spacing=6)
                   for n in range(n0, n1 + 1)]
            parts.append(ft.Container(ft.Column(old, spacing=1), bgcolor=ft.Colors.with_opacity(0.10, "#DC2626"), padding=po(4, 2, 4, 2), border_radius=3, border=ft.Border(left=ft.BorderSide(3, "#DC2626"))))
        new = [ft.Row([ft.Container(width=22), ft.Text(x, size=13, color=RD_GREEN, weight=ft.FontWeight.W_500, expand=True)], spacing=6) for x in shown.split(chr(10))]
        parts.append(ft.Container(ft.Column(new, spacing=1), bgcolor=ft.Colors.with_opacity(0.12, "#16A34A"), padding=po(4, 2, 4, 2), border_radius=3, border=ft.Border(left=ft.BorderSide(3, "#16A34A"))))
        foot = ft.Row([ft.Row(_rd_chips(f), spacing=3, tight=True), ft.Text(f.reason or f.finding, size=10, color=C["paper_dim"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS, expand=True),
                       _rd_btn("Accept", ft.Icons.CHECK_ROUNDED, lambda e, g=f, o=opt: rd_accept(g, o), True), _rd_btn("Reject", ft.Icons.CLOSE_ROUNDED, lambda e, g=f: rd_reject(g), False)],
                      spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        box = ft.Container(ft.Column([*parts, ft.Container(foot, padding=po(26, 1, 2, 0))], spacing=3), margin=po(0, 3, 0, 6))
        box.key = ft.ScrollKey("rd:" + _fd_key(f))
        return box

    def rd_rebuild():
        """The whole lyrics (rhyme colours as in the colour column), with every open AI suggestion as a diff over / under its lines (red = now, green = suggested): Accept or Reject each one."""
        text = lyr()
        tl = text.split(chr(10))
        sugs = cc_suggestions(text, True)
        total = sum(len(v) for v in sugs.values())
        rc = RC.colorize(text, cc_current_result(), song_lang())
        segmap = {n: sg for n, sg in RC.line_segments(text, rc)}

        def line_row(n: int):
            segs = segmap.get(n)
            if not segs:
                return ft.Container(height=10)
            tag = len(segs) == 1 and segs[0][1] is None and RC.is_tag_line(segs[0][0])
            spans = [ft.TextSpan(t, style=_rc_style(rc, sp) if sp else ft.TextStyle(color=C["paper_dim"] if tag else C["ink"])) for t, sp in segs]
            return ft.Container(ft.Row([ft.Text(str(n), size=10, color=C["paper_dim"], width=22), ft.Text(spans=spans, size=13, expand=True)], spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
                                padding=po(4, 2, 4, 2))
        rows, upto = [], 0
        for n in range(1, len(tl) + 1):
            items = sugs.get(n, [])
            if items:
                for k, (f, shown, opt, n1) in enumerate(items):
                    rows.append(_rd_hunk(f, shown, opt, n, n1, tl, k == 0 or n1 != items[0][3]))
                upto = max(upto, max(i[3] for i in items))
            elif n > upto:
                rows.append(line_row(n))
        if not total:
            rows.insert(0, ft.Text("No suggestions are waiting. Everything has been accepted, rejected or no longer matches the lyrics.", size=12, color=C["paper_dim"]))
        rdv["body"].controls = rows
        rdv["title"].value = f"{total} suggestion(s) waiting" if total else "No suggestions waiting"
        safe_update(rdv["body"], rdv["title"])

    async def rd_scroll():
        await asyncio.sleep(0.4)
        try:
            await rdv["body"].scroll_to(scroll_key="rd:" + rdv["focus"], duration=250)
        except Exception as ex:
            log.debug("review scroll_to failed: %r", ex)

    def rd_accept(f, opt):
        sess, _rv = review_state()
        in_report = sess is not None and any(g.id == f.id and g.spec == f.spec for r in sess.specreports.values() for g in r.findings)
        in_director = f.spec == "music_director" and any(g.id == f.id for g in dr_cue_findings())
        if sess is not None and not in_report and not in_director:
            return
        fd_apply(f, opt)
        rd_rebuild()

    def rd_reject(f):
        fd_ignore(f)
        rd_rebuild()

    def rd_all(accept: bool):
        done = 0
        for _ in range(200):                                       # each Accept can remove a conflicting alternative, so the list is read again every time
            sugs = cc_suggestions(lyr(), True)
            first = next(((f, o) for n in sorted(sugs) for f, _sh, o, _n1 in sugs[n][:1]), None)
            if first is None:
                break
            f, o = first
            if accept:
                if not fd_apply(f, o):
                    fd_ignore(f)                                      # it cannot be applied (the program's checks): it is not offered again
            else:
                fd_ignore(f)
            done += 1
        rd_rebuild()

    def open_review_diff(focus: "str | None" = None):
        if time.monotonic() < rdv.get("until", 0.0):
            return                                                   # a double click: one dialog only
        rdv["until"] = time.monotonic() + 30.0
        try:
            _open_review_diff(focus)
        finally:
            rdv["until"] = time.monotonic() + 1.5

    def _open_review_diff(focus: "str | None" = None):
        rdv["title"] = ft.Text("", size=12, color=C["text2"])
        rdv["body"] = ft.Column([], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)
        rdv["focus"] = focus
        rd_rebuild()
        page.show_dialog(ft.AlertDialog(title=ft.Row([ft.Text("Review AI suggestions", size=18, weight=ft.FontWeight.BOLD), rdv["title"]], spacing=12, vertical_alignment=ft.CrossAxisAlignment.END),
                                        bgcolor=C["panel"], content=ft.Container(width=int(max(420, min(820, page_w() - 120))), height=dlg_h(680, 0.82), bgcolor=C["paper"], border_radius=8, padding=po(8, 6, 4, 6), content=rdv["body"]),
                                        actions=[ft.TextButton("Reject all", on_click=lambda e: rd_all(False)), ft.TextButton("Accept all", on_click=lambda e: rd_all(True)),
                                                 ft.FilledButton("Close", on_click=lambda e: page.pop_dialog(), style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))]))
        if focus:
            page.run_task(rd_scroll)

    def ask_specialist(f):
        """Open the chat of the specialist that wrote this finding, with the finding written into the message box (the user reads, edits and sends it). No AI call, no new session, no lyrics added here: the
        message carries the finding itself, so it makes sense even when this chat is a newer session that never saw the report."""
        sess, _rv = review_state()
        if f.spec in SP.CHAT_IDS:
            set_spec(f.spec)
        pick_ai_tab("chat")
        parts = [f"About finding {f.id} by {SP.name_of(f.spec)} ({'line ' + str(f.first) if f.first == f.last else 'lines ' + str(f.first) + '-' + str(f.last)}):" if f.first else f"About finding {f.id} by {SP.name_of(f.spec)} (whole song):"]
        if f.evidence:
            parts.append("Original:\n" + f.evidence)
        if f.suggestion:
            parts.append("Suggested:\n" + f.suggestion)
        if f.finding:
            parts.append("Finding: " + f.finding)
        if f.reason:
            parts.append("Reason: " + f.reason)
        if sess is not None and sess.report_hash and sess.report_hash != LA.text_hash(lyr()):
            parts.append("(The lyrics have changed since this report was made.)")
        chat_tf.value = "\n".join(parts) + "\n\nMy question: "
        chat_tf.max_lines = 8
        safe_update(chat_tf)
        try:
            page.run_task(chat_tf.focus)
        except Exception:
            pass

    def continue_chat(spec: str):
        """The chat icon of a report part: open that specialist's chat in the left panel, ready for the next question. No AI call; the report is already part of that specialist's session."""
        if spec in SP.CHAT_IDS:
            set_spec(spec)
        pick_ai_tab("chat")
        try:
            page.run_task(chat_tf.focus)
        except Exception:
            pass

    def _chat_icon(spec: str):
        return ft.TextButton(f"Continue in chat with {SP.name_of(spec)}", icon=ft.Icons.CHAT_BUBBLE_OUTLINE_ROUNDED, on_click=lambda e, s=spec: continue_chat(s),
                             tooltip="Open this specialist's chat on the left; it already knows this report", style=ft.ButtonStyle(color=C["accent"], padding=po(4, 0, 4, 0)))

    def _rep_spec_section(sess, spec: str) -> list:
        """The content of one specialist's section: summary, strengths, findings, uncertainties (from its report; an older report is shown with a note, its suggestions are re-checked before Apply)."""
        r = sess.specreports.get(spec)
        st, msg = rep_state(sess, spec)
        if r is None or r.status != "ok":
            return [ft.Text(("This part failed: " + _friendly(msg)) if st == "error" else "Not available yet. Use Retry in the Overview.", size=11, color=C["red"] if st == "error" else C["text3"], selectable=True)]
        out = []
        if st == "outdated":
            out.append(ft.Text(f"Outdated: this report was made for earlier lyrics or with an earlier prompt ({r.ts}). Suggestions are re-checked against the current lyrics before they can be applied.", size=11, color=C["amber"]))
        arch = SP.is_producer(spec)
        if arch:
            out.append(ft.Text("Song Structure", size=11, weight=ft.FontWeight.BOLD, color=C["text2"]))
        if r.summary:
            out.append(ft.Text(r.summary, size=12, color=C["text1"], selectable=True))
        if r.strengths:
            out.append(ft.Text("What works in the existing structure" if arch else "What works", size=11, weight=ft.FontWeight.BOLD, color=C["green"]))
            out += [ft.Text("• " + x, size=11, color=C["text2"], selectable=True) for x in r.strengths]
        if r.findings and arch:
            named = {k for _t, ks in ARCH_GROUPS for k in ks}
            for title, kinds in ARCH_GROUPS:
                fs = [f for f in r.findings if (f.kind in kinds if kinds else f.kind not in named)]
                if fs:
                    out.append(ft.Text(title, size=11, weight=ft.FontWeight.BOLD, color=C["text2"]))
                    out += [_fd_card(f, sess, compact=_fd_applicable(f)) for f in fs]
            plain = [f for f in r.findings if _fd_applicable(f)]
            if plain:
                out.append(_fd_review_btn(plain, sess))
        elif r.findings:
            out.append(ft.Text("Suggestions", size=11, weight=ft.FontWeight.BOLD, color=C["text2"]))
            out += [_fd_card(f, sess) for f in r.findings]
        elif not r.strengths:
            out.append(ft.Text("Nothing important needs to change in this area.", size=11, color=C["green"]))
        if r.uncertain:
            out.append(ft.Text("Not sure / left out", size=11, weight=ft.FontWeight.BOLD, color=C["amber"]))
            out += [ref_text(x, "• ", size=10, color=C["text3"], selectable=True) for x in r.uncertain]
        if r.diagnostics:
            log.debug("report diagnostics %s: %s", spec, r.diagnostics)
            out.append(ft.Text(I18N.tr(CG.PARTIAL), size=10, color=C["amber"]))
        return out

    def _rep_archive(sess) -> list:
        """The saved reports of the former specialists (Lyric Craft Advisor, Song Editor, Thai Prosody Expert) of THIS sheet: text only, read-only (no Apply), never part of the priority list or of the Songwriter AI's session."""
        out = []
        for sp_id in SP.ARCHIVE_IDS:
            r = (getattr(sess, "archive", None) or {}).get(sp_id)
            if r is None or r.status != "ok":
                continue
            out.append(ft.Text(f"{SP.name_of(sp_id)} · {r.ts}", size=11, weight=ft.FontWeight.BOLD, color=C["text2"]))
            if r.summary:
                out.append(ft.Text(r.summary, size=11, color=C["text1"], selectable=True))
            out += [ft.Text("• " + x, size=10, color=C["text2"], selectable=True) for x in r.strengths]
            for f in r.findings:
                out.append(ft.Text(("Line " + (str(f.first) if f.first == f.last else f"{f.first}-{f.last}") + ": " if f.first else "") + f.finding + (f"  → {f.suggestion}" if f.suggestion else ""), size=10, color=C["text3"], selectable=True))
        return out

    def _friendly(msg: str) -> str:
        """What the user is told about a failed part. A backend error ("AI error: timed out") is useful as it is; the program's own parser sentences are not: they stay in the log."""
        msg = msg or ""
        return msg if msg.startswith(("AI error", "The prompt of")) else "the AI's answer could not be read. Try again."

    def _rep_part_row(sess, spec: str, label: str):
        st, msg = rep_state(sess, spec)
        partial = st == "ok" and bool(sess.specreports.get(spec) is not None and sess.specreports[spec].diagnostics)
        icon, col = {"ok": (ft.Icons.CHECK_CIRCLE_ROUNDED, C["green"]), "error": (ft.Icons.ERROR_ROUNDED, C["red"]), "outdated": (ft.Icons.HISTORY_ROUNDED, C["amber"]),
                     "running": (ft.Icons.HOURGLASS_TOP_ROUNDED, C["accent"]), "missing": (ft.Icons.RADIO_BUTTON_UNCHECKED_ROUNDED, C["text3"])}[st]
        state_txt = "Partial" if partial else {"ok": "Done", "error": "Failed", "outdated": "Outdated", "running": "Working…", "missing": "Not done"}[st]
        if partial:
            icon, col = ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED, C["amber"]
        row = [ft.Icon(icon, size=16, color=col), ft.Text(label, size=12, weight=ft.FontWeight.W_600, color=C["text1"]), ft.Text(state_txt, size=11, color=col)]
        if spec in SP.CHAT_IDS:
            row.append(ft.IconButton(ft.Icons.CHAT_BUBBLE_OUTLINE_ROUNDED, icon_size=16, width=28, height=28, padding=0, icon_color=C["accent"], tooltip=f"Continue in chat with {SP.name_of(spec)}", on_click=lambda e, s=spec: continue_chat(s)))
        if st in ("error", "outdated", "missing") and not rep_busy() and not S.an_busy:
            row.append(ft.TextButton("Retry", on_click=lambda e, p=spec: page.run_task(rep_retry, p, spec_snapshot()), style=ft.ButtonStyle(color=C["accent"], padding=po(4, 0, 4, 0))))
        out = [ft.Row(row, spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True)]
        if st == "error" and msg:
            out.append(ft.Text(_friendly(msg), size=10, color=C["red"], selectable=True))
        return ft.Column(out, spacing=0, horizontal_alignment=ft.CrossAxisAlignment.START)

    def _sec_head(title: str, color: str, count: "int | None" = None):
        kids = [ft.Container(width=4, height=16, bgcolor=color, border_radius=2), ft.Text(title, size=13, weight=ft.FontWeight.BOLD, color=C["text1"])]
        if count:
            kids.append(ft.Container(ft.Text(str(count), size=10, weight=ft.FontWeight.BOLD, color="#FFFFFF"), bgcolor=color, border_radius=9, padding=po(7, 1, 7, 1)))
        return ft.Row(kids, spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _report_card(controls: list, bar: "str | None" = None):
        return ft.Container(ft.Column(controls, spacing=6, horizontal_alignment=ft.CrossAxisAlignment.START), bgcolor=C["panel2"], border_radius=10, padding=po(12, 10, 12, 10),
                            border=ft.Border(left=ft.BorderSide(3, bar)) if bar else None)

    def _rf_line_text(n: int, fresh: bool, lines_txt: list, sec: dict) -> list:
        """The spans of one line of the rhyme map: the line as written, with the rhyme words the Rhyme Analyzer named coloured at the position where the word is found in the line. Exact = bold on a tint, near =
        italic with a wavy underline, uncertain = underlined only. Nothing is coloured when the word is not found, or when the lyrics changed after the report (the stored positions belong to the old text)."""
        row = next((x for x in sec["lines"] if x["line"] == n), None)
        text = lines_txt[n - 1] if 0 < n <= len(lines_txt) else ""
        if not fresh or not text:
            return [ft.TextSpan(row["ending"] if row and row["ending"] else "…", style=ft.TextStyle(color=C["text2"]))]
        marks = []
        shared = {x["label"] for x in sec["lines"] if x["label"] and sum(1 for y in sec["lines"] if y["label"] == x["label"]) >= 2}
        if row and row.get("pos") and row.get("label") in shared and not row.get("repeat"):          # a letter no other line has, or a repeated word (repetition is not a new rhyme): nothing to colour
            marks.append((row["pos"][0], row["pos"][1], LI.PALETTE[RF.group_colour_index(row["label"])], "near" if row.get("near") else "exact"))
        for k, pr in enumerate(sec["pairs"]):
            if pr["placement"] == "end":
                continue                                              # an end rhyme is coloured by the letter of its line
            for side in ("a", "b"):
                w = pr[side]
                if w["line"] == n and w.get("pos"):
                    marks.append((w["pos"][0], w["pos"][1], LI.PALETTE[(4 + k) % len(LI.PALETTE)], pr["sound"]))
        marks.sort(key=lambda m: (m[0], m[1]))
        out, at = [], 0
        for a_, b_, col, sound in marks:
            if a_ < at or b_ > len(text) or a_ >= b_:
                continue                                              # overlapping or outside the text: not drawn
            if a_ > at:
                out.append(ft.TextSpan(text[at:a_], style=ft.TextStyle(color=C["text1"])))
            if sound == "near":
                st = ft.TextStyle(color=col, italic=True, decoration=ft.TextDecoration.UNDERLINE, decoration_color=col, decoration_style=ft.TextDecorationStyle.WAVY)
            elif sound == "uncertain":
                st = ft.TextStyle(color=col, decoration=ft.TextDecoration.UNDERLINE, decoration_color=col, decoration_style=ft.TextDecorationStyle.DOTTED)
            else:
                st = ft.TextStyle(color=col, weight=ft.FontWeight.BOLD, bgcolor=ft.Colors.with_opacity(0.18, col))
            out.append(ft.TextSpan(text[a_:b_], style=st))
            at = b_
        if at < len(text):
            out.append(ft.TextSpan(text[at:], style=ft.TextStyle(color=C["text1"])))
        return out

    RF_PRIORITY_COL = {"optional": "text3", "worthwhile": "amber", "important": "red"}

    def _rf_pair_rows(pairs: list, tone: str = "text1") -> list:
        rows = []
        for pr in pairs:
            a, b = pr["a"], pr["b"]
            where = f"L{a['line']:02d} ↔ L{b['line']:02d}" if a["line"] != b["line"] else f"L{a['line']:02d} " + I18N.tr("(inside the line)")
            head = (f"{where}: {a['word']} – {b['word']}  ·  {I18N.tr(RF.KIND_LABEL.get(pr['kind'], 'Sound link'))}" if pr["kind"] == "repetition" else
                    f"{where}: {a['word']} – {b['word']}  ·  {I18N.tr(RF.KIND_LABEL.get(pr['kind'], 'Sound link'))}, "
                    f"{I18N.tr(RF.PLACEMENT_LABEL.get(pr['placement'], pr['placement']))}, {I18N.tr(pr['sound'])}")
            sub = [I18N.tr("Confidence") + ": " + I18N.tr(str(pr.get("confidence") or "medium"))]
            if pr.get("why"):
                sub.append(pr["why"])
            rows.append(ft.Container(ft.Column([ft.Text(head, size=11, color=C[tone], selectable=True)] + [ft.Text(t, size=10, color=C["text3"]) for t in sub if t], spacing=0),
                                     padding=po(2, 2, 2, 2), ink=True, on_click=lambda e, n=a["line"]: goto_file_line(n)))
        return rows

    def _rhyme_flow_block(flow: list, r, sess):
        """Rhyme & Flow inside the Songwriter's report. For each section: the scheme the PROGRAM derived from the confirmed end rhymes, the line map with the confirmed rhyme words coloured, the confirmed pairs with
        their kind, placement, sound and confidence, repetition shown apart (not a rhyme), the pairs the program could not confirm or disproved (never coloured), and under them the Songwriter's quality
        analysis and recommendation. Built once with the report; opening or closing a section draws nothing again. A click on a line goes to it in the editor."""
        text = lyr() if S.song else ""
        lines_txt = text.split(chr(10))
        fresh = bool(r is not None and r.lyrics_hash and r.lyrics_hash == LA.text_hash(text))
        have_ev = bool((r.data or {}).get("rhyme_evidence"))
        py_ = bool(have_ev and (r.data or {}).get("rhyme_evidence", {}).get("source") == PRE.SOURCE)
        tiles = []
        if not have_ev:
            tiles.append(ft.Text(I18N.tr(CG.UNREADABLE) + " " + I18N.tr("No scheme or pair is shown. Below is the Songwriter's reading of the flow only."), size=11, color=C["amber"]))
        else:
            ev_ = (r.data or {}).get("rhyme_evidence", {})
            py_ = ev_.get("source") == PRE.SOURCE
            tiles.append(ft.Text("Python Rhyme Analysis: the sounds, letters and pairs were worked out by Python with sound rules from the spelling. It is a help, not a verdict: it can miss internal rhymes, words of several syllables and words it cannot read, and it can pair repeated or very distant words. English is not confirmed by sound. No pair listed does not mean no rhyme." if py_ else "The sounds, the letters and the schemes are the Rhyme Analyzer AI's own reading; the program only checked the format of its answer.", size=10, color=C["text3"]))
            incomplete = bool(ev_.get("notes") or ev_.get("left_out") or any(s_.get("inconsistent") for s_ in flow))
            if incomplete:                                                            # one short status for the whole report; every detail goes to the log
                log.debug("rhyme report incomplete: notes=%s left_out=%s inconsistencies=%s", ev_.get("notes"), ev_.get("left_out"),
                          [(s_.get("name"), s_.get("inconsistencies")) for s_ in flow if s_.get("inconsistent")])
                tiles.append(ft.Text(I18N.tr(CG.PARTIAL_SHORT), size=11, weight=ft.FontWeight.BOLD, color=C["amber"]))
        for sec in flow:
            key = f"rf:{sec['name']}:{sec['first']}"
            rep_open.setdefault(key, len(flow) <= 4)
            body = []
            if sec.get("pattern") or sec.get("scheme_name"):
                body.append(ft.Text(I18N.tr("Scheme") + ": " + (sec["pattern"] or "–") + (f"  ({sec['scheme_name']})" if sec.get("scheme_name") and sec["scheme_name"] != sec["pattern"] else ""), size=11, weight=ft.FontWeight.BOLD, color=C["text2"]))
            probs_ = RF.section_problems(sec) if (fresh and have_ev and not py_ and not (r.data or {}).get("live")) else []          # a Python result has no AI answer to re-check
            unreadable_ = bool(fresh and have_ev and not py_ and not (r.data or {}).get("live") and _section_unreadable(sec))
            if unreadable_ and not probs_:
                probs_ = ["no end group was read for any line of this section"]
            if probs_:
                sid_ = section_id_at(sec["first"])
                if sid_:
                    body.append(_recheck_box(sid_, probs_, "", unreadable_))
            counts = {}
            for x in sec["lines"]:
                if x["label"]:
                    counts[x["label"]] = counts.get(x["label"], 0) + 1
            for x in sec["lines"]:
                n, lab = x["line"], x["label"]
                alone = bool(lab) and counts.get(lab, 0) < 2
                plain = alone or bool(x.get("repeat"))                                # a letter only one line has, or a repeated word: a grey badge, not a rhyme colour
                tip = (f"{x['ending']} = {x['sound']}" if x.get("sound") else "") + ((" · " if x.get("sound") else "") + I18N.tr("Repetition (not a rhyme)") if x.get("repeat") else "")
                if not lab:
                    tip = I18N.tr("Python found no rhyme partner for this line. That is not proof that it has none (Python can miss internal rhymes, words of several syllables and words it cannot read)." if py_ else "No rhyme group was reported for this line. This is missing information, not a statement that it has no rhyme.")
                badge = ft.Container(ft.Text((lab + ("~" if x.get("near") else "")) if lab else "—", size=11, weight=ft.FontWeight.BOLD, color=C["text3"] if (plain or not lab) else "#FFFFFF"),
                                     width=22, height=22, border_radius=6, alignment=ft.Alignment(0, 0), tooltip=tip or None,
                                     bgcolor=LI.PALETTE[RF.group_colour_index(lab)] if (lab and not plain) else C["field"])
                body.append(ft.Container(ft.Row([badge, ft.Text(f"L{n:02d}", size=10, color=C["text3"], width=30), ft.Text(spans=_rf_line_text(n, fresh, lines_txt, sec), size=12, expand=True, selectable=False)],
                                                spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                                         ink=True, border_radius=6, padding=po(2, 2, 2, 2), tooltip=f"Go to line {n} in the editor", on_click=lambda e, n=n: goto_file_line(n)))
            if sec["pairs"]:
                body.append(ft.Text("Rhyme connections", size=11, weight=ft.FontWeight.BOLD, color=C["text2"]))
                body += _rf_pair_rows(sec["pairs"])
            elif sec["lines"]:
                body.append(ft.Text("Python found no rhyme pair in this section (it can miss internal rhymes, words of several syllables and words it cannot read)." if py_ else "The Rhyme Analyzer reported no rhyme pair in this section.", size=10, color=C["text3"]))
            if sec["repetitions"]:
                body.append(ft.Text("Repetition (not a rhyme)", size=11, weight=ft.FontWeight.BOLD, color=C["text2"]))
                body += _rf_pair_rows(sec["repetitions"], "text2")
            if sec["not_confirmed"]:
                body.append(ft.Text("Not confirmed: Python could only estimate these from the spelling (English)" if py_ else "Not confirmed: the AI called these rhymes, the program could not read them", size=11, weight=ft.FontWeight.BOLD, color=C["amber"]))
                nc_rows = _rf_pair_rows(sec["not_confirmed"], "text3")
                if len(sec["not_confirmed"]) > 3:                                      # many: closed behind one row, never a long list
                    body.append(_rep_tile(f"rfnc:{sec['name']}:{sec['first']}", f"Not confirmed ({len(sec['not_confirmed'])})", nc_rows))
                else:
                    body += nc_rows
            if sec.get("misplaced"):
                body.append(ft.Text("The Analyzer named a word that is not in the line it points to (a wrong line number or text); not shown as a rhyme", size=10, weight=ft.FontWeight.BOLD, color=C["amber"]))
                body += _rf_pair_rows(sec["misplaced"], "text3")
            if sec["rejected"]:
                body.append(ft.Text("Not rhymes: " + ", ".join(f"{p['a']['word']}–{p['b']['word']}" for p in sec["rejected"]) + " (the sounds differ)", size=10, color=C["text3"]))
            c = sec.get("critique")
            if c is None:
                body.append(ft.Text("The Songwriter wrote no reading for this section.", size=10, color=C["text3"]))
            else:
                if c["quality"]:
                    body += [ft.Text("Lyrics quality", size=11, weight=ft.FontWeight.BOLD, color=C["text2"]), ref_text(c["quality"], size=12, color=C["text1"], selectable=True)]
                if c.get("checks"):
                    body.append(ref_text(c["checks"], I18N.tr("Checked:") + " ", size=10, color=C["text3"], selectable=True))
                if c.get("strength"):
                    body += [ft.Text("Strength", size=11, weight=ft.FontWeight.BOLD, color=C["green"]), ref_text(c["strength"], size=12, color=C["text1"], selectable=True)]
                if c.get("issue"):
                    body += [ft.Text("Issue", size=11, weight=ft.FontWeight.BOLD, color=C["red"]), ref_text(c["issue"], size=12, color=C["text1"], selectable=True)]
                if c.get("consideration") or c.get("consider"):
                    body += [ft.Text("To consider", size=11, weight=ft.FontWeight.BOLD, color=C["amber"]), ref_text(c.get("consideration") or c.get("consider"), size=12, color=C["text1"], selectable=True)]
                if c.get("rhyme"):
                    body += [ft.Text("Rhyme contribution", size=11, weight=ft.FontWeight.BOLD, color=C["text2"]), ref_text(c["rhyme"], size=12, color=C["text1"], selectable=True)]
                if c["limits"]:
                    body.append(ref_text(c["limits"], "Not judged: ", size=10, color=C["amber"], selectable=True))
                unsure = bool(sec["not_confirmed"] or sec["uncertain"])
                if c["keep"]:
                    body.append(ft.Row([ft.Icon(ft.Icons.CHECK_CIRCLE_OUTLINE_ROUNDED, size=14, color=C["green"]),
                                        ft.Text("Recommendation: nothing needs to change in the writing; the pairs marked not confirmed above were not judged." if unsure else "Recommendation: keep it as it is; nothing needs to change.",
                                                size=11, color=C["green"], expand=True)], spacing=6))
                    if c.get("keep_reason") or c.get("reason"):
                        body.append(ref_text(c.get("keep_reason") or c["reason"], I18N.tr("Why it can stay:") + " ", size=11, color=C["text2"], selectable=True))
                else:
                    col = RF_PRIORITY_COL.get(c["priority"], "text3")
                    chip = ft.Container(ft.Text(I18N.tr(c["priority"].capitalize()), size=10, weight=ft.FontWeight.BOLD, color=C[col]), border=ft.Border.all(1, C[col]), border_radius=10, padding=po(7, 1, 7, 1))
                    body.append(ft.Container(ft.Column([ft.Row([ft.Text("Recommendation", size=11, weight=ft.FontWeight.BOLD, color=C["accent"]), chip], spacing=8),
                                                        ref_text(c["recommendation"], size=12, color=C["text1"], selectable=True)] + ([ref_text(c["reason"], "Why: ", size=11, color=C["text2"], selectable=True)] if c["reason"] else [])
                                                        + ([ref_text(c["benefit"], I18N.tr("Expected benefit:") + " ", size=11, color=C["text2"], selectable=True)] if c.get("benefit") else []),
                                                       spacing=3), bgcolor=C["panel"], border_radius=8, padding=po(8, 6, 8, 6)))
            tiles.append(_rep_tile(key, f"{sec['name']} — {sec.get('pattern') or '…'}", body, f"lines {sec['first']}-{sec['last']}"))
        rep_open.setdefault("rf", True)
        return _rep_tile("rf", "Rhyme & Flow", tiles, f"{len(flow)} section(s)")

    REPORT_STATE = {"ok": ("Done", "green"), "error": ("Failed", "red"), "outdated": ("Outdated", "amber"), "running": ("Working…", "accent"), "missing": ("Not written yet", "text3")}

    def _report_body(sess, spec: str) -> list:
        """The report of ONE assistant as a page that reads top to bottom: who and how it went (one card with the summary), what works, what to fix first (the top suggestions, each with its own
        Apply / Ignore), the other suggestions grouped and closed, then what the assistant was unsure of. The Songwriter's page ends with the rhyme report."""
        r = sess.specreports.get(spec)
        st, msg = rep_state(sess, spec)
        txt, ckey = REPORT_STATE[st]
        col = C[ckey]
        partial = st == "ok" and r is not None and bool(r.diagnostics)
        if partial:
            txt, col = "Partial", C["amber"]
        chip = ft.Container(ft.Text(txt, size=10, weight=ft.FontWeight.BOLD, color=col), border=ft.Border.all(1, col), border_radius=10, padding=po(8, 1, 8, 1))
        head = [ft.Row([ft.Text(SP.name_of(spec), size=15, weight=ft.FontWeight.BOLD, color=C["text1"]), chip], spacing=8, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER)]
        if r is not None and getattr(r, "ts", ""):
            head.append(ft.Text(f"Written {r.ts}", size=10, color=C["text3"]))
        if st == "error" and msg:
            head.append(ft.Text(_friendly(msg), size=11, color=C["red"], selectable=True))
        if st == "outdated":
            head.append(ft.Text("The lyrics changed after this report. Each suggestion is checked against the current lyrics before it can be applied.", size=11, color=C["amber"]))
        if r is not None and r.status == "ok" and r.summary:
            head.append(ref_text(r.summary, size=13, color=C["text1"], selectable=True))
        actions = []
        if r is not None and r.status == "ok" and spec in SP.CHAT_IDS and not (r.data or {}).get("live"):       # the chat button appears when the report is finished
            actions.append(_chat_icon(spec))
        if st in ("error", "outdated", "missing") and not rep_busy() and not S.an_busy:
            actions.append(ft.TextButton("Write again", icon=ft.Icons.REFRESH_ROUNDED, on_click=lambda e, p=spec: page.run_task(rep_retry, p, spec_snapshot()),
                                         style=ft.ButtonStyle(color=C["accent"], padding=po(4, 0, 4, 0))))
        if actions:
            head.append(ft.Row(actions, spacing=6, wrap=True))
        out = [_report_card(head, col)]
        live = bool(r is not None and (r.data or {}).get("live"))          # still being written: only what has arrived is shown
        if r is not None and r.status == "ok":
            if r.strengths:
                out.append(_report_card([_sec_head("What works", C["green"])] + [ref_text(x, "• ", size=12, color=C["text2"], selectable=True) for x in r.strengths], C["green"]))
            if spec == "songwriter":                                          # the rhyme part of THIS report: checked evidence (Rhyme Analyzer + the program) with the Songwriter's critique under it
                flow = RF.merge((r.data or {}).get("rhyme_evidence"), (r.data or {}).get("rhyme_flow") or [])
                if flow:
                    out.append(_rhyme_flow_block(flow, r, sess))
                elif not live:
                    out.append(_report_card([ft.Text("This report has no Rhyme & Flow part (it was written before that existed, or the AI left it out). Press Write the report to get it.", size=11, color=C["text3"])], C["text3"]))
            if live:                                                          # a suggestion can only be applied from the finished report (its id and wording are final then)
                out.append(ft.Text("Still writing: the suggestions and the notes appear when the report is finished.", size=11, color=C["text3"], italic=True))
                return out
            top = SE.aggregate([r], limit=5)
            keys_top = {_fd_key(f) for f in top}
            if top:
                out.append(_sec_head("Fix first", C["amber"], len(top)))
                out += [_fd_card(f, sess) for f in top]
            elif not r.findings and not live:
                out.append(_report_card([ft.Text("Nothing important needs to change in this area.", size=12, color=C["green"])], C["green"]))
            rest = [f for f in r.findings if _fd_key(f) not in keys_top]
            if rest:
                out.append(_sec_head("Other suggestions", C["accent"], len(rest)))
                if SP.is_producer(spec):
                    named = {k for _t, ks in ARCH_GROUPS for k in ks}
                    groups = [(title, [f for f in rest if (f.kind in kinds if kinds else f.kind not in named)]) for title, kinds in ARCH_GROUPS]
                else:
                    by_kind: dict = {}
                    for f in rest:
                        by_kind.setdefault(f.kind or "other", []).append(f)
                    groups = [(RV_FOCUS.get(k, k.replace("_", " ").capitalize()), fs) for k, fs in by_kind.items()]
                for title, fs in groups:
                    if fs:
                        out.append(_rep_tile(f"grp:{spec}:{title}", title, [_fd_card(f, sess) for f in fs], f"{len(fs)}"))
            plain = [f for f in r.findings if _fd_applicable(f)]
            if plain and SP.is_producer(spec):
                out.append(_fd_review_btn(plain, sess))
            if r.uncertain:
                out.append(_rep_tile(f"unsure:{spec}", "Not sure / left out", [ref_text(x, "• ", size=11, color=C["text3"], selectable=True) for x in r.uncertain], f"{len(r.uncertain)}"))
            if r.diagnostics and not live:
                log.debug("report body diagnostics %s: %s", spec, r.diagnostics)
                out.append(_rep_tile(f"diag:{spec}", "Notes from checking this report", [ft.Text(I18N.tr(CG.PARTIAL), size=10, color=C["amber"])] + [ft.Text("• " + d_, size=10, color=C["text2"], selectable=True) for d_ in RPT.diag_lines(r.diagnostics)],
                                     f"{len(r.diagnostics)} · a suggestion that could not be verified is not shown"))
        return out

    def render_review():
        """The report box of the right panel: the Song Analysis Report (Analyze Song) and/or the suggestions of a scoped Review. Read-only for the lyrics."""
        sess, rv = review_state()
        show = bool(sess and (sess.report_on or rv))
        if not show:
            review_title.value, review_ring.visible, review_again.disabled = "Song Analysis Report", False, True
            review_warn.visible = review_err.visible = review_summary.visible = False
            review_list.controls = [ft.Text("Press “Write the report” above: the report of this assistant appears here, one suggestion at a time. The Songwriter AI hands in the rhyme report too.", size=12, color=C["text3"])]
            safe_update(review_box, review_ring, review_title, review_again, review_warn, review_err, review_summary, review_list)
            return
        res, busy, err = rv.get("result") if rv else None, bool(rv and rv.get("busy")), (rv or {}).get("error", "")
        scope = res.scope if res else (rv or {}).get("scope")
        full = bool(sess.report_on)
        review_ring.visible = (busy or rep_busy()) and not full           # a report's progress shows in the top bar (beside the ComfyUI status), not as a spinner in the page
        review_title.value = "Song Analysis Report" if full else (("AI Review — " + scope.label) if scope else "AI Review")
        review_title.visible = review_again.visible = review_close.visible = not full             # a report page has its own header card; the scoped Review keeps this row
        review_again.content = "Analyze Again" if full else "Review Again"
        review_again.disabled = busy or rep_busy() or not S.song
        review_err.value, review_err.visible = ((f"The review failed: {err}" if (err and not full) else ""), bool(err and not full))
        same_sheet = bool(res and S.song and S.song.book.active_id == res.sheet_id and S.song.id == res.song_id)
        cur = LA.text_hash(lyr())
        stale = bool(res and res.scope.lyrics_hash != cur and same_sheet)
        outdated_all = bool(full and sess.report_hash and sess.report_hash != cur)
        review_warn.value = ("Outdated: the lyrics changed after this analysis. Rhyme colours are hidden until you analyze again; each suggestion is checked against the current lyrics before it is applied."
                             if outdated_all else
                             "The lyrics changed since this review. Each suggestion is checked against the current lyrics before it is applied. Press Review Again for a fresh review." if stale else "")
        review_warn.visible = bool(review_warn.value)
        if full:
            review_summary.value, review_summary.visible = "", False
        else:
            review_summary.value, review_summary.visible = ((res.summary or ("The lyrics work as they are." if res.verdict == "ok" else "")) if res else ("Asking the AI…" if busy else "")), True
        cards = []
        fd_view.update(reg={}, seen={}, btns=[], bar=None, where=(S.song.id, S.song.book.active_id) if S.song else None)
        if full:
            if _fd_all(sess):
                fd_view["bar"] = ft.Row(_fd_bar_controls(sess), spacing=6, wrap=True)
                cards.append(fd_view["bar"])
            cards += _report_body(sess, report_view["k"])
        sug = []
        if res:
            if res.verdict == "ok" and not res.items:
                sug.append(ft.Text("Nothing important needs to change here.", size=12, color=C["green"]))
            sug += [_rv_card(n, it, res, same_sheet) for n, it in enumerate(res.items, 1)]
            if res.ignored:
                log.debug("review ignored: %s", res.ignored)
                sug.append(ft.Text(I18N.tr(CG.PARTIAL), size=10, color=C["text3"]))
        elif busy:
            sug.append(ft.Text("Asking the AI…", size=11, color=C["text3"]))
        if sug:
            cards += [_rep_tile("sg", "Review suggestions (selection / section)", sug)] if full else sug
        review_list.controls = cards
        safe_update(review_box, chat_view, review_ring, review_title, review_again, review_close, review_err, review_warn, review_summary, review_list)
        _cc_after()

    render_review = _perf(render_review)                       # MS_PERF=1 only: the timing of the full report drawing, of an Apply / Ignore card update, of a jump to the lyrics and of the Undo / Redo sync
    fd_apply, fd_refresh, goto_finding, fd_sync, fd_set_filter = _perf(fd_apply), _perf(fd_refresh), _perf(goto_finding), _perf(fd_sync), _perf(fd_set_filter)

    def load_spec_reports(sess):
        """Flow A: the saved report of every specialist of THIS sheet comes back from its session file. No AI call."""
        sess.specreports = {}
        for sp_id in SP.ANALYZE_ORDER:
            try:
                r = SE.load_session(sess.song.id, sess.song.book.active_id, SP.analysis_id(sp_id)).latest_report()
            except Exception:
                r = None
            if r is not None:
                r.spec = sp_id
                sess.specreports[sp_id] = r
        sess.archive = {}                                                  # the reports of the former specialists: kept in their own session files, only read here
        for sp_id in SP.ARCHIVE_IDS:
            try:
                r = SE.load_session(sess.song.id, sess.song.book.active_id, sp_id).latest_report()
            except Exception:
                r = None
            if r is not None:
                sess.archive[sp_id] = r
        sess.report_on = bool(sess.specreports) or bool(sess.archive) or sess.report_on          # a song that only has the reports of the former specialists shows them (read-only) too

    def start_report(only: "str | None" = None):
        """The button of a report panel (`only` = that specialist) or AI Actions → Analyze Song (both, one after the other). The song, sheet and lyrics are captured now; each step re-checks them before it starts."""
        if not S.song:
            toast("Open or create a song first.", C["amber"]); return
        if rep_busy() or S.chat_busy_song or S.an_busy:
            toast("The AI is still busy. Try again in a moment.", C["amber"]); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        text = lyr()
        if not LA.sung_lines(text):
            toast("Write some lyrics first (there are only [tags] or empty lines).", C["amber"]); return
        bad = [f"{SP.name_of(i)}: {' '.join(p.errors)}" for i, p in SP.reload_all().items() if i in SP.ANALYZE_ORDER and (only is None or i == only) and not p.ok]
        if bad:
            toast("A specialist prompt is not valid, so nothing was sent: " + " | ".join(bad)[:300], C["amber"]); return
        sess = sessions.get(S.song.id)
        sess.report_on, sess.report_hash = True, LA.text_hash(text)
        report_view["k"] = only or report_view["k"]                             # the report that runs is the one shown
        rp_show_tab("report")
        page.run_task(run_report, S.song.id, text, S.song.book.active_id, spec_snapshot(), only)

    def spec_snapshot() -> dict:
        s = S.song
        return {"song_id": s.id, "sheet_id": s.book.active_id, "title": s.title, "lyrics": lyr(), "lyrics_hash": LA.text_hash(lyr()), "genre": s.settings.genre, "mood": s.settings.mood,
                "energy": s.settings.energy, "vocal": s.settings.vocal, "bpm": s.settings.bpm, "language": s.settings.language, "backend": S.backend,
                "timesignature": s.settings.timesignature, "duration": s.settings.duration, "live": {},
                "caption": (s.settings.style_prompt if s.settings.style_mode == "advanced" else ""),          # only a caption the user wrote is known before Generate (Basic writes it at Generate)
                "corrections": li_corrections()}

    async def rhyme_evidence_for(snap: dict):
        """The Rhyme Analyzer's answer for the Songwriter's report: a saved result for exactly these lyrics is used (no request), otherwise the Analyzer runs ONCE first. What the Analyzer said is laid out as it
        gave it (the program only checked the FORMAT: no sound is re-judged, no scheme worked out). When the Analyzer cannot be used or its answer cannot be read this returns None: the report then says so
        and the Songwriter writes without rhyme evidence. The program never makes up a substitute."""
        try:
            if not (S.song and S.song.id == snap["song_id"] and lyr() == snap["lyrics"]):
                return None
            if FF.PYTHON_RHYME_PRIMARY:                                     # the Python Rhyme Analysis (the same map as the rhyme colours): no Rhyme Analyzer AI request, no saved AI result
                rep["step"], rep["spec"] = "Python rhyme analysis", "songwriter"
                render_review()
                return await asyncio.get_running_loop().run_in_executor(None, PRE.build, snap["lyrics"], snap.get("language") or "")
            res = cc_current_result()
            if res is None:
                rep["step"], rep["spec"] = "Rhyme Analyzer: reading the sounds", "songwriter"
                render_review()
                await run_rhymes(False)
                res = cc_current_result()
            if res is None or getattr(res, "schema", "") != R6.SCHEMA or not getattr(res, "llm", None):
                log.info("rhyme evidence: the Analyzer gave no readable answer for these lyrics")
                return None
            return RF.build_llm_evidence(res.llm, snap["lyrics"])
        except Exception as ex:
            log.warning("rhyme evidence could not be gathered: %r", ex)
            return None

    async def run_spec_report(spec: str, snap: dict):
        """One specialist of Analyze Song (the Songwriter AI or the Song Architect): one request into its own session, answered as a structured report. A failure is stored for this specialist only."""
        sess_ui = sessions.get(snap["song_id"])
        if sess_ui is None:
            return
        loop = asyncio.get_running_loop()
        reply_lang = "Thai" if I18N.lang() == "th" else "English"
        if SP.is_producer(spec) and eng_state(sess_ui, "syl")[0] == "ok" and lyr() == snap["lyrics"]:
            try:                                                      # the syllable counts of the Prosody analysis of EXACTLY these lyrics, as information for the Architect
                effs, _x = LA.effective_lines(sess_ui.analysis["result"], sess_ui.an_corr)
                snap = dict(snap, counts={e.line.file_line: e.count for e in effs})
            except Exception:
                pass
        try:                                                          # Live Lyrics Insights: counted by Python from THIS snapshot (same lyrics, same settings), no extra LLM call
            lv = snap.get("live") or {}
            ins = await loop.run_in_executor(None, LI.analyze, snap["lyrics"], snap.get("corrections") or {})
            dn = LI.density(ins, LI.DensitySettings(bpm=float(snap.get("bpm") or 120), timesig=str(snap.get("timesignature") or "4"), duration=float(snap.get("duration") or 180)))
            snap = dict(snap, facts=(LI.producer_block(ins, dn, snap.get("caption") or "") if SP.is_producer(spec) else LI.songwriter_block(ins, dn, rhyme=False)))
            TRC.log_event("live_facts", specialist=spec, lyrics_hash=snap.get("lyrics_hash"), facts=snap["facts"])      # kept in the specialist trace: what the program told the AI
        except Exception as ex:
            log.warning("live insights facts were not added to the %s request: %r", spec, ex)
        if spec == "songwriter":
            snap = dict(snap, sound=SE.sound_evidence(snap["lyrics"]))      # the Thai line-end sounds, worked out by the program
            ev = await rhyme_evidence_for(snap)                             # the Rhyme Analyzer (only when there is no saved result for these exact lyrics) and the program's checks
            snap = dict(snap, rhyme_data=ev, rhyme_evidence=(PRE.evidence_text(ev) if (ev or {}).get("source") == PRE.SOURCE else RF.evidence_text(ev)))
        ask = spec_ask(spec, 0.4, "analyze")
        S.an_busy, S.an_kind, S.an_progress = snap["song_id"], "spec", f"{SP.name_of(spec)}…"
        render_review()
        try:
            model = an_identity(snap["backend"])[0]
        except Exception:
            model = ""
        rep_obj = None
        live_prior = {"set": False, "rep": None}                            # the report that was on screen before this run (the half-written one is never kept)
        try:
            task_user = SE.analyze_user(snap, reply_lang)
            if SP.is_producer(spec):
                note = CL.repair_notes(snap["lyrics"])
                if note:
                    task_user += "\n\n" + note
            stepper = None
            if RPT.ENABLED:                                                  # step by step: the data, the summary, one section at a time, the suggestions; each part is shown as soon as it is read
                SE.drop_live(snap["song_id"], snap["sheet_id"], SP.analysis_id(spec))         # a fresh conversation for this report (the old one would only fill the context)
                _sp = sessions.get(snap["song_id"])
                live_prior.update(set=True, rep=(_sp.specreports.get(spec) if _sp is not None else None))
                base_step = rep["step"]

                def _live_step(txt: str):
                    rep["step"] = (base_step + " · " if base_step else "") + txt
                    S.an_progress = txt
                    loop.call_soon_threadsafe(render_review)

                def _show_part():
                    try:
                        part = SE.parse_report(stepper.partial_doc(), snap["lyrics"], spec)
                    except ValueError:
                        return                                              # nothing readable yet
                    part.lyrics_hash, part.backend, part.model = snap["lyrics_hash"], snap["backend"], model
                    part.data["live"] = True                                # still being written: the page says so and shows only what has arrived
                    if spec == "songwriter" and snap.get("rhyme_data"):
                        ev_ = dict(snap["rhyme_data"])
                        ev_["sections"] = list(ev_.get("sections") or [])[:stepper.reviewed]      # the table holds the sections whose review has arrived
                        part.data["rhyme_evidence"] = ev_
                    loop.call_soon_threadsafe(_apply_report_part, snap, part)
                stepper = RPT.ReportStepper(spec, snap, ask, task_user, _live_step, lambda: not (sessions.get(snap["song_id"]) and sessions[snap["song_id"]].song.lyrics == snap["lyrics"]), _show_part,
                                            reopen=lambda: SE.drop_live(snap["song_id"], snap["sheet_id"], SP.analysis_id(spec)), reply_language=reply_lang,
                                            compact=snap["backend"] == "grok")       # the conversation is full: a new session, the data again, then the next part
                raw = await loop.run_in_executor(None, stepper.run)
                if stepper.stray:
                    log.debug("report steps stray tags (%s): %s", spec, stepper.stray)
                for inc_ in stepper.incomplete:                             # an answer that never reached [DONE]: not used, the raw text is kept in the log
                    log.warning("report part %s (attempt %s) is incomplete_response: no [DONE]; not used. raw=%r", inc_["part"], inc_["attempt"], (inc_["raw"] or "")[:6000])
            else:
                raw = await loop.run_in_executor(None, ask, SE.analyze_task(spec), task_user)
            try:
                rep_obj = SE.parse_report(raw, snap["lyrics"], spec)
                if stepper is not None and stepper.problems:
                    log.debug("report steps problems (%s): %s", spec, stepper.problems)
                    rep_obj.diagnostics += stepper.problems                 # a part that never arrived: the report says it is partial
                if stepper is not None and stepper.diagnostics:
                    rep_obj.diagnostics += [d_ for d_ in stepper.diagnostics if d_ not in rep_obj.diagnostics]          # text that was refused (copied format text, another part's tags, text outside the tags)
                    log.warning("report steps refused text (%s): %s | raw answers=%r", spec, stepper.diagnostics, [(a_["part"], a_["attempt"], (a_["raw"] or "")[:3000]) for a_ in stepper.answers])
                if spec == "songwriter" and snap.get("rhyme_data"):
                    rep_obj.data["rhyme_evidence"] = snap["rhyme_data"]     # kept with the report: the evidence it was written from, tied to its lyrics hash
                if spec == "songwriter":
                    rep_obj.diagnostics += RF.quality_gate(rep_obj.data.get("rhyme_flow") or [], rep_obj.findings, snap.get("rhyme_data"), rep_obj.summary)          # the shape of the report only: no sound judgement, nothing changed
            except ValueError as ex:
                log.warning("specialist %s report was not usable: %s | raw=%r", spec, ex, raw[:6000])
                rep_obj = SE.SpecReport(spec, "error", error="The AI's answer could not be read.", raw=raw)
                rep_obj.diagnostics = [str(ex)]
        except Exception as ex:
            log.warning("specialist %s failed: %r", spec, ex)
            rep_obj = SE.SpecReport(spec, "error", error=f"AI error: {ex}")
        finally:
            S.an_busy, S.an_kind, S.an_progress = "", "", ""
            if live_prior["set"]:
                _sp = sessions.get(snap["song_id"])
                cur_ = _sp.specreports.get(spec) if _sp is not None else None
                if cur_ is not None and (cur_.data or {}).get("live"):
                    if live_prior["rep"] is None:
                        _sp.specreports.pop(spec, None)
                    else:
                        _sp.specreports[spec] = live_prior["rep"]
        rep_obj.lyrics_hash, rep_obj.backend, rep_obj.model, rep_obj.ts = snap["lyrics_hash"], snap["backend"], model, SE.now()
        try:
            rep_obj.prompt_hash = SP.load(spec).hash
        except Exception:
            pass
        TRC.log_event("report", specialist=spec, song_id=snap["song_id"], sheet_id=snap["sheet_id"], backend=snap["backend"], model=model, lyrics_hash=snap["lyrics_hash"], lyrics=snap["lyrics"],
                      status=rep_obj.status, error=rep_obj.error, summary=rep_obj.summary[:400], findings=len(rep_obj.findings), applicable=sum(1 for f in rep_obj.findings if f.suggestion),
                      notes=rep_obj.uncertain[:12], diagnostics=rep_obj.diagnostics[:20], raw_answer=rep_obj.raw)
        store_spec_report(snap, rep_obj)

    def _apply_report_part(snap: dict, part):
        """A part of the report that is still being written: shown at once, never saved. Only while the same song, sheet and lyrics are open."""
        sess_ui = sessions.get(snap["song_id"])
        if sess_ui is None or sess_ui.song.book.active_id != snap["sheet_id"] or sess_ui.song.lyrics != snap["lyrics"] or not rep["running"]:
            return
        sess_ui.specreports[part.spec] = part
        render_review()

    def store_spec_report(snap: dict, rep_obj):
        """Keep a finished report: in the specialist's session file (a successful one) and, when the sheet it was made for is still the open one, on screen. An old result never replaces the report of other lyrics."""
        if rep_obj.status == "ok":
            s_obj = get_spec_session(snap["song_id"], snap["sheet_id"], SP.analysis_id(rep_obj.spec))
            SE.add_report(s_obj, rep_obj)
            SE.save_session(s_obj)
        sess_ui = sessions.get(snap["song_id"])
        if sess_ui is not None and sess_ui.song.book.active_id == snap["sheet_id"]:
            old = sess_ui.specreports.get(rep_obj.spec)
            if rep_obj.status != "ok" and old is not None and old.status == "ok":
                toast(f"{SP.name_of(rep_obj.spec)} failed: {rep_obj.error[:200]}. The earlier report is kept.", C["amber"])       # a failed answer never damages a report that was good
            elif old is not None and old.status == "ok" and old.lyrics_hash != rep_obj.lyrics_hash and S.song and S.song.id == snap["song_id"] and old.lyrics_hash == LA.text_hash(lyr()):
                pass                                            # this answer was made for older lyrics: it never replaces the report of the lyrics that are open now
            else:
                sess_ui.specreports[rep_obj.spec] = rep_obj
                sess_ui.fd_status ={k: v for k, v in sess_ui.fd_status.items() if not k.startswith(rep_obj.spec + ":")}
        render_review()

    def clear_reports_on_screen(song_id: str, specs: list):
        """Pressing "Write the report" starts from an empty page: the earlier report of these assistants is taken off the screen at once, so an old and a new report are never mixed. (The saved
        copy in the session file is not touched; it is replaced only when the new report is finished.)"""
        sess_ui = sessions.get(song_id)
        if sess_ui is None:
            return
        for sp in specs:
            sess_ui.specreports.pop(sp, None)
            sess_ui.fd_status = {k: v for k, v in sess_ui.fd_status.items() if not k.startswith(sp + ":")}

    async def run_report(song_id: str, text0: str, sheet0: str, snap: "dict | None" = None, only: "str | None" = None):
        rep["running"] = True
        clear_reports_on_screen(song_id, [only] if only else list(SP.ANALYZE_ORDER))
        render_review(); refresh_actions_ui()
        try:
            snap = snap or spec_snapshot()                                     # taken synchronously at the click: the whole job works on THIS song, sheet and lyrics
            specs = [only] if only else list(SP.ANALYZE_ORDER)
            for i, spec in enumerate(specs, 1):
                if not (S.song and S.song.id == song_id and S.song.book.active_id == sheet0 and lyr() == text0):
                    break                                              # the song, sheet or lyrics changed: nothing more is started; what finished stays (tied to its snapshot)
                rep["step"], rep["spec"] = f"{i}/{len(specs)} {SP.name_of(spec)}", spec
                render_review()
                try:
                    await run_spec_report(spec, snap)
                except Exception as ex:
                    log.warning("report step %s failed: %r", spec, ex)
        finally:
            rep["running"], rep["step"], rep["spec"] = False, "", ""
            render_review(); refresh_actions_ui()

    async def rep_retry(spec: str, snap: "dict | None" = None):
        """Retry ONE specialist (no other specialist is asked again), on the snapshot taken when the button was pressed."""
        if not S.song or rep_busy() or S.an_busy or S.chat_busy_song:
            toast("The AI is still busy. Try again in a moment.", C["amber"]); return
        snap = snap or spec_snapshot()
        rep["running"], rep["step"], rep["spec"] = True, SP.name_of(spec), spec
        clear_reports_on_screen(snap["song_id"], [spec])
        render_review()
        try:
            await run_spec_report(spec, snap)
        except Exception as ex:
            log.warning("report retry %s failed: %r", spec, ex)
        finally:
            rep["running"], rep["step"], rep["spec"] = False, "", ""
            render_review(); refresh_actions_ui()

    def open_alternatives():
        """AI Actions → Find Alternative Words: for the words the user selected (the Thesaurus engine; the result opens in a dialog). Never run for every word of the song."""
        if not S.song:
            toast("Open or create a song first.", C["amber"]); return
        sess = sessions.get(S.song.id)
        if sess is not None and not sess.thes_sel:               # not captured yet (it is captured when the selection settles) or collapsed by the click on the menu: use what the user had just selected
            a, b = S.sel
            rs = _recent_sel
            if not b > a and rs["v"] and (rs["t"] == 0.0 or time.time() - rs["t"] < 2.5):
                a, b = rs["v"]
            text_now = lyr()
            if 0 <= a < b <= len(text_now):
                sess.thes_sel = {"start": a, "end": b, "text": text_now[a:b]}
        sel, msg = th_selection(sess) if sess else (None, "")
        if sel is None:
            toast(msg or "Select a word or a short phrase in the lyrics first.", C["amber"]); return
        toast("Finding alternatives…")
        page.run_task(show_alternatives)

    async def show_alternatives():
        await run_thesaurus(False)
        sess = sessions.get(S.song.id) if S.song else None
        info = (sess.thes if sess else None) or {}
        if "result" not in info or not thes_fresh(sess):
            err = info.get("last_error")
            if err:
                toast("Could not get alternatives: " + str(err.get("message", ""))[:160], C["amber"])
            return
        res = info["result"]
        cards = [_th_card(n, it, lambda: page.pop_dialog()) for n, it in enumerate(res.items, 1)] or [ft.Text("No suitable alternatives found", size=12, color=C["text3"])]
        page.show_dialog(ft.AlertDialog(title=ft.Text("Alternative words"), bgcolor=C["panel"], content=ft.Container(width=420, height=dlg_h(min(420, 150 + 120 * max(1, len(cards)))), content=ft.Column(
            [ft.Text(f"for “{res.phrase}”  (line {res.line}" + (f" · {res.section}" if res.section else "") + "). Nothing is changed until you press the replace icon (Undo restores it).", size=11, color=C["text3"]), *cards],
            spacing=8, scroll=ft.ScrollMode.AUTO)), actions=[ft.TextButton("Close", on_click=lambda e: page.pop_dialog())]))

    def start_review(mode: str = "auto", note: str = ""):
        """The Review button: the part to review is captured NOW (selection, else the section of the cursor), never re-read from the caret later."""
        if not S.song:
            toast("Open or create a song first.", C["amber"]); return
        if S.chat_busy_song or S.an_busy or rep_busy():
            toast("The AI is still busy. Try again in a moment.", C["amber"]); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        text = lyr()
        scope = RV.make_scope(text, tuple(S.sel), mode)
        log.debug("review start: mode=%s sel=%s -> %s", mode, tuple(S.sel), scope)
        if isinstance(scope, str):
            toast(scope, C["amber"]); return
        rp_show_tab("report")
        page.run_task(run_review, S.song.id, scope, text, note)

    def open_review_note():
        tf = ft.TextField(label="What do you want the AI to look at?", multiline=True, min_lines=2, max_lines=5, **field_style(text_size=13, height=None))

        def go(e):
            page.pop_dialog()
            start_review("auto", tf.value or "")
        page.show_dialog(ft.AlertDialog(title=ft.Text("Review with a note"), bgcolor=C["panel"], content=ft.Container(width=380, content=tf),
                                        actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.FilledButton("Review", on_click=go, style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))]))

    async def run_review(song_id: str, scope, text: str, note: str = ""):
        sess = sessions.get(song_id)
        if sess is None or not S.song or S.song.id != song_id:
            return
        sheet0, title = S.song.book.active_id, S.song.title
        settings = {"genre": S.song.settings.genre, "mood": S.song.settings.mood, "language": getattr(S.song.settings, "language", "")}
        backend, host = S.backend, S.host
        try:
            model, _p = an_identity(backend)
        except Exception:
            model = ""
        decisions = list(sess.review_dec)[-12:]
        key = RV.cache_key(text, scope, title, backend, model, note, decisions)
        reply_lang = "Thai" if I18N.lang() == "th" else "English"
        sess.review = {"busy": True, "scope": scope}
        S.an_busy, S.an_kind, S.an_progress = song_id, "review", "Asking the AI…"
        render_review(); refresh_actions_ui()
        result, error = None, ""
        hit = rv_cache.get(key)
        if hit is not None:
            result = copy.deepcopy(hit)
            for it in result.items:
                it.status = "open"
        else:
            loop = asyncio.get_running_loop()

            ask = spec_ask("songwriter", 0.4, "review")                            # one request of the Songwriter AI's own session
            try:
                result = await loop.run_in_executor(None, lambda: RV.review(text, scope, title, settings, ask, reply_lang, note, decisions))
            except LA.AnalysisError as ex:
                error = str(ex)
                log.warning("review failed: %s | raw=%r", ex, (ex.raw or "")[:6000])
            except Exception as ex:
                error = f"AI error: {ex}"
                log.warning("review AI error: %r", ex)
            finally:
                S.an_busy, S.an_kind, S.an_progress = "", "", ""
        S.an_busy, S.an_kind, S.an_progress = "", "", ""
        refresh_actions_ui()
        sess = sessions.get(song_id)
        if sess is None:
            return
        if result is not None:
            rv_cache.put(key, copy.deepcopy(result))
            log.debug("review raw: %r | parsed: %r | dropped: %r", result.raw, [(i.focus, i.first, i.last, i.original, i.suggestion) for i in result.items], result.ignored)
        if sess.song.book.active_id != sheet0 or sess.song.lyrics != text:         # the sheet or the lyrics changed while the AI was answering: the answer describes other lyrics
            sess.review = None
            if S.song and S.song.id == song_id:
                toast("The lyrics changed while the AI was working, so that review was not used. Press Review again.", C["amber"])
            render_review(); return
        if result is None:
            sess.review = {"error": error, "scope": scope}
        else:
            result.song_id, result.sheet_id = song_id, sheet0
            sess.review = {"result": result}
        render_review()

    def rv_preview(item, on_apply=None):
        """What the lines look like before and after this one suggestion (the lyrics are not touched). `on_apply` is what the Apply button of the preview does (default: apply a review suggestion)."""
        text = lyr()
        loc = SE.locate_text(item, text)
        if isinstance(loc, str):
            toast(loc, C["amber"]); return
        a, b = loc
        lines = text.split("\n")
        lo, hi = max(1, item.first - 2), min(len(lines), item.last + 2)
        c0 = sum(len(l) + 1 for l in lines[:lo - 1])
        c1 = c0 + sum(len(l) + 1 for l in lines[lo - 1:hi]) - 1

        def block(mid: str, color: str):
            return ft.Container(ft.Text(spans=[ft.TextSpan(text[c0:a], style=ft.TextStyle(color=C["text2"])), ft.TextSpan(mid, style=ft.TextStyle(color=color, bgcolor=ft.Colors.with_opacity(0.2, color), weight=ft.FontWeight.BOLD)),
                                                ft.TextSpan(text[b:c1], style=ft.TextStyle(color=C["text2"]))], size=13, selectable=True), bgcolor=C["field"], border_radius=8, padding=10)

        applying = {"on": False}

        def apply_now(e):
            if applying["on"]:
                return                                           # a second quick click: not applied twice
            again = SE.locate_text(item, lyr())
            if isinstance(again, str):                           # the lines changed while the preview was open: it stays open and says why
                toast(again, C["amber"]); return
            applying["on"] = True
            if (on_apply or (lambda: rv_apply(item)))() is False:    # it was not applied (the reason was shown): the preview stays open
                applying["on"] = False
                return
            page.pop_dialog()
            page.run_task(_focus_editor_if_lyrics)
        page.show_dialog(ft.AlertDialog(title=ft.Text("Preview"), bgcolor=C["panel"], content=ft.Container(width=460, content=ft.Column([
            ft.Text("Before", size=11, color=C["text3"]), block(item.original, C["red"]), ft.Text("After", size=11, color=C["text3"]), block(item.suggestion, C["green"]),
            ft.Text(item.reason, size=11, color=C["text2"], selectable=True)], spacing=6, tight=True, scroll=ft.ScrollMode.AUTO)),
            actions=[ft.TextButton("Close", on_click=lambda e: (page.pop_dialog(), page.run_task(_focus_editor_if_lyrics))), ft.FilledButton("Accept", on_click=apply_now, style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))]))

    def rv_apply(item):
        """Apply ONE suggestion: only for the song and sheet it was made for, only where its quoted text is found exactly once in the CURRENT lyrics. One Undo step."""
        sess, rv = review_state()
        res = rv.get("result") if rv else None
        if res is None or not S.song or S.song.id != res.song_id:
            toast("The song changed. Nothing was applied.", C["amber"]); return
        if S.song.book.active_id != res.sheet_id:
            toast("This review was made for another sheet. Open that sheet to apply it. Nothing was changed.", C["amber"]); return
        if item.status != "open":
            return
        got = SE.apply_change(item, lyr(), "songwriter")
        if isinstance(got, str):
            toast(got, C["amber"]); render_review(); return
        new_text, sel = got
        set_lyrics(new_text, sel, refocus=False)
        item.status = "applied"
        sess.review_dec.append(("applied", item.original, item.suggestion))
        toast("Applied (Undo restores the previous lyrics)", C["green"])
        render_review()

    def rv_ignore(item):
        sess, rv = review_state()
        if rv is None or item.status != "open":
            return
        item.status = "ignored"
        sess.review_dec.append(("ignored", item.original, item.suggestion))
        render_review()

    # Ask the AI about one take that was not liked: the take's own snapshot is the context (never the sheet that is open now) --------------------------------------------------
    def open_take_feedback(song_id: str, t: M.Take):
        if not (S.song and S.song.id == song_id):
            toast("Open this song first, then ask about its take.", C["amber"]); return
        if S.chat_busy_song or S.an_busy:
            toast("The AI is still busy. Try again in a moment.", C["amber"]); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        tf = ft.TextField(label="What did you not like about this take?", multiline=True, min_lines=3, max_lines=6, **field_style(text_size=13, height=None))
        hint = ft.Text("The AI has not heard the take. It answers from the lyrics, the music caption and the settings that made it, and from what you write here.", size=11, color=C["text3"])

        def go(e):
            q = (tf.value or "").strip()
            if not q:
                return
            page.pop_dialog()
            page.run_task(run_take_feedback, song_id, t, q)
        page.show_dialog(ft.AlertDialog(title=ft.Text(f"Ask AI about Take {t.number:02d}"), bgcolor=C["panel"], content=ft.Container(width=400, content=ft.Column([tf, hint], spacing=8, tight=True)),
                                        actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.FilledButton("Ask", on_click=go, style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))]))

    async def run_take_feedback(song_id: str, t: M.Take, question: str):
        if not S.song or S.song.id != song_id or S.chat_busy_song or S.an_busy:
            return
        system, user = TF.build_messages(t, question, lyr(), "Thai" if I18N.lang() == "th" else "English")
        backend, host = S.backend, S.host
        append_chat(song_id, M.ChatMessage("user", f"About Take {t.number:02d}: {question}", specialist="songwriter"))
        S.chat_busy_song = song_id
        chat_busy.visible = True; safe_update(chat_busy)
        refresh_actions_ui()
        loop = asyncio.get_running_loop()
        try:
            if backend == "grok":
                g = LC.grok_creds()
                raw = await loop.run_in_executor(None, lambda: LC.ask_grok(g["key"], g["model"], system, user, temperature=0.5))
            else:
                raw = await loop.run_in_executor(None, lambda: LC.ask_local(host, LLM_DIR / backend.split(":", 1)[1], system, user, temperature=0.5))
            log.debug("take feedback raw: %r", raw)
            shown, notes = TF.present(raw)
            if notes:
                log.debug("take feedback notes: %s", notes)
            if shown:
                append_chat(song_id, M.ChatMessage("assistant", shown, specialist="songwriter"))
            else:
                append_chat(song_id, M.ChatMessage("note", I18N.tr("the AI's answer could not be read. Try again."), specialist="songwriter"))
        except Exception as ex:
            log.warning("take feedback failed: %r", ex)
            append_chat(song_id, M.ChatMessage("note", I18N.tr("the AI's answer could not be read. Try again."), specialist="songwriter"))
        finally:
            S.chat_busy_song = ""
            chat_busy.visible = False; safe_update(chat_busy)
            refresh_actions_ui()

    def pick_ai_tab(k: str):
        S.ai_tab = "chat"                                                # the left panel is the Chat only; the analysis lives in the Right Panel
    ai_col = panel(ft.Column([ft.Row([ft.Text("Songwriting Assistant", size=14, weight=ft.FontWeight.BOLD, color=C["text1"]), chat_busy, ft.Container(expand=True),
                                      ibtn(ft.Icons.ADD_COMMENT_OUTLINED, "New session: the AI forgets this conversation and the earlier messages are cleared", new_session),
                                      ibtn(ft.Icons.DELETE_SWEEP_OUTLINED, "Clear this song's chat", clear_chat)], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                              chat_view], spacing=8, expand=True), padding=12)
    ai_holder = ft.Container(content=ai_col, width=S.ai_w)

    # Lyrics Editor (centre) with the Lyrics Tools to its right -------------------------------------------------------------------
    def toggle_tools(e=None):
        """The Lyrics Tools are the first tab of the Right Panel: this opens it."""
        rp_show_tab("tools")
    btn_tools = tbtn(ft.Icons.BUILD_CIRCLE_OUTLINED, "Open the Lyrics Tools (Right Panel)", toggle_tools)
    # The sidebar: the tools that fit the caret on top (see refresh_context), the whole old toolbox under "All Tools" (closed at the start). One scroll area for all of it.
    tools_col.scroll = None
    tools_col.visible = True

    def toggle_all_tools(e=None):
        tools_col.visible = not tools_col.visible
        all_tools_btn.icon = ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED if tools_col.visible else ft.Icons.KEYBOARD_ARROW_RIGHT_ROUNDED
        safe_update(tools_col, all_tools_btn)
    all_tools_btn = ft.TextButton("All Tools", icon=ft.Icons.KEYBOARD_ARROW_DOWN_ROUNDED, on_click=toggle_all_tools, style=ft.ButtonStyle(color=C["text2"], padding=po(4, 0, 4, 0)),
                                  tooltip="Every tool: sections, instrumental parts, vocal delivery, backing vocals, timing and your own tools")
    ctx_box = ft.Column([ctx_title, ctx_sub, ctx_dynamic, ctx_view_lyrics, ctx_view_empty], spacing=6)
    # The Lyrics Tools tab is a RIBBON: groups of small icon buttons with the group's name below (hover for what a tool does), the cue editor of the tag at the cursor first. Groups wrap onto
    # a second row when the Workspace is narrow; nothing has to be scrolled to be found.
    RIBBON = [("structure", "Sections", 3), ("instrumental", "Instrumental", 2), ("vocal", "Vocal Delivery", 6), ("backing", "Backing", 2), ("timing", "Timing & Editing", 2)]

    def ribbon_btn(key: str, icon, color, tip: str):
        inner = ft.Container(ft.Icon(icon, size=20, color=color), width=36, height=36, border_radius=6, alignment=ft.Alignment(0, 0), ink=True, bgcolor="#00000000",
                             on_click=lambda e, k=key: apply_tool(k),
                             tooltip=ft.Tooltip(message=tip, padding=po(12, 9, 12, 9), bgcolor="#0A1426", text_style=ft.TextStyle(size=12, color=C["text1"], height=1.45), prefer_below=False,
                                                decoration=ft.BoxDecoration(bgcolor="#0A1426", border=ft.Border.all(1, C["accent"]), border_radius=8),
                                                wait_duration=ft.Duration(milliseconds=250), show_duration=ft.Duration(seconds=14), size_constraints=ft.BoxConstraints(max_width=360)))

        def hover(e):
            inner.bgcolor = C["panel2"] if e.data in (True, "true") else "#00000000"
            safe_update(inner)
        inner.on_hover = hover
        tool_keys.add(key)
        return ft.Draggable(group="lyrics_tool", data=key, content=inner, affinity=ft.Axis.HORIZONTAL,
                            content_feedback=ft.Container(ft.Icon(icon, size=20, color="#FFFFFF"), bgcolor=C["accent"], padding=8, border_radius=6))

    def ribbon_group(title: str, tools: list, cols: int, color: str):
        """One group of the tool panel: its name on top, the icon buttons in a row that wraps to the width of the panel."""
        return ft.Column([ft.Text(title, size=10, weight=ft.FontWeight.W_600, color=C["text3"]),
                          ft.Row([ribbon_btn(k, ic, color, tip) for k, _lbl, ic, tip in tools], wrap=True, spacing=2, run_spacing=2)], spacing=4)

    ribbon_cursor = ft.Column([ft.Row([ctx_title, ctx_sub], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True), ctx_dynamic,
                               ft.Text("At the cursor", size=10, color=C["text3"])], spacing=4, width=TOOLS_W)
    tools_view = ft.Container(expand=True)
    tools_mode = {"wide": None}

    def tools_layout(force: bool = False):
        """Builds the ribbon: again when the custom tools change or the Workspace changes between wide and narrow. Wide: the cue editor of the tag at the cursor comes first. Narrow: the icon groups
        come first (they are what is reached for most) and the cue editor follows."""
        try:
            wide = ws_width() >= 760
        except NameError:
            wide = True
        if wide == tools_mode["wide"] and not force:
            return
        tools_mode["wide"] = wide
        groups = []
        for gid, title, cols in RIBBON:
            g = next(x for x in TOOL_GROUPS if x[0] == gid)
            groups.append(ribbon_group(title, [t for _pt, tl in g[5] for t in tl], cols, g[4]))
        custom = [(t.key, t.name, ft.Icons.EXTENSION_ROUNDED, f"{t.name}  (custom tool: {PS.TOOL_TYPES.get(t.type, t.type)})\n" + (t.description + "\n" if t.description else "")
                   + (f"Inserts: {t.content}" if t.type == "snippet" else f"Wraps the selected text: {t.prefix}text{t.suffix}")) for t in pstore.tools]
        if custom:
            groups.append(ribbon_group("Custom", custom, 3, "#34D399"))
        items = []
        for i, g in enumerate(groups):
            if i:
                items.append(ft.Divider(height=1, thickness=1, color=C["border"]))
            items.append(g)
        tools_view.content = ft.Column(items, spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
        safe_update(tools_view)
    ribbon_hook["fn"] = lambda: tools_layout(force=True)
    tools_layout(force=True)

    def start_lyric_format(mode: str):
        """Lyric Formatter: one mode, the lyrics as they are now. The program does it by rules (no AI, no waiting); the result replaces the editor only when every word, section and [Hold] survived."""
        if not S.song:
            toast("Open or create a song first.", C["amber"]); return
        text = lyr()
        if not text.strip():
            toast("Write some lyrics first.", C["amber"]); return
        fmt_ui["token"] += 1
        req = LF.FormatRequest(fmt_ui["token"], S.song.id, S.song.book.active_id, text, mode)
        done = LF.python_plan(req)
        if done.action == "reject":
            toast(done.reason, C["amber"]); return
        new_text, record = LF.commit(req.lyrics, done)
        if not record:
            toast(LF.REASON_SAME, C["green"]); return
        set_lyrics(new_text, S.sel)
        toast("Lyrics formatted.", C["green"])

    async def run_lyric_format(req, system: str, backend: str, host: str, token: int):
        """One stateless call (no specialist session, no chat history). A late or bad answer never replaces the lyrics."""
        loop = asyncio.get_running_loop()
        raw, err = "", ""
        try:
            user = LF.user_message(req.mode, req.lyrics)
            raw = await loop.run_in_executor(None, lambda: spec_llm(LF.with_contract(system), user, backend, host, 0.2))
        except Exception as ex:
            err = str(ex)
            log.warning("lyric formatter failed: %s", err[:200])
        try:
            now_song = S.song.id if S.song else ""
            now_sheet = S.song.book.active_id if S.song else ""
            now_lyrics = lyr() if S.song else ""
            done = LF.plan(req, raw, now_song_id=now_song, now_sheet_id=now_sheet, now_lyrics=now_lyrics, now_token=fmt_ui["token"], error=err, already_applied=token in fmt_ui["done"])
            if done.action == "reject":
                toast(done.reason, C["amber"]); return
            new_text, record = LF.commit(req.lyrics, done)
            if not record:
                toast(LF.REASON_SAME, C["green"]); return
            if not S.song or S.song.id != req.song_id or S.song.book.active_id != req.sheet_id or lyr() != req.lyrics:
                toast(LF.REASON_EDITED if S.song and lyr() != req.lyrics else LF.REASON_SONG, C["amber"]); return
            fmt_ui["done"].add(token)
            set_lyrics(new_text, S.sel)
            toast("Lyrics formatted.", C["green"])
        finally:
            if fmt_ui["token"] == token:
                if S.an_kind == "format":
                    S.an_busy, S.an_kind, S.an_progress = "", "", ""
                if S.chat_busy_song == req.song_id:
                    S.chat_busy_song = ""
                chat_busy.visible = bool(S.chat_busy_song)
                if fmt_ui["ring"] is not None:
                    fmt_ui["ring"].visible = False
                refresh_actions_ui()
                safe_update(*(c for c in (chat_busy, fmt_ui["ring"]) if c is not None))

    format_ring = ft.ProgressRing(width=16, height=16, stroke_width=2, color=C["accent"], visible=False)
    fmt_ui["ring"] = format_ring
    lyric_format_btn = tpop(ft.Icons.AUTO_FIX_HIGH_ROUNDED, "Lyric Formatter: pick a mode. The program formats these lyrics by rules and replaces them immediately; no word is changed. Undo brings the previous lyrics back.",
                            [ft.PopupMenuItem(content=ft.Text(LF.mode_label(mode_id)), on_click=lambda e, m=mode_id: start_lyric_format(m)) for mode_id in LF.MODE_IDS], ai=True, caret=True)
    fmt_ui["btn"] = lyric_format_btn
    analyze_btn = tbtn(ft.Icons.INSIGHTS_ROUNDED, "Analyze Song: syllables, rhymes, rhythm and songwriting quality in one report, with suggestions you can apply", lambda e: start_report(), ai=True)
    review_pop = tpop(ft.Icons.RATE_REVIEW_OUTLINED, "More AI options",
                      [ft.PopupMenuItem(content=ft.Text("Review the selection / section (suggestions only)"), on_click=lambda e: start_review()),
                       ft.PopupMenuItem(content=ft.Text("Review the whole song"), on_click=lambda e: start_review("all")),
                       ft.PopupMenuItem(content=ft.Text("Review with a note…"), on_click=lambda e: open_review_note()),
                       ft.PopupMenuItem(content=ft.Text("Find alternative words (selected)"), on_click=lambda e: open_alternatives())], ai=True, caret=True)
    struct_btn = tbtn(ft.Icons.LIBRARY_MUSIC_ROUNDED, "Song Structures: pick a built-in or your own structure. With no sung lyrics it inserts the section tags; with lyrics the AI arranges YOUR lines into it (you preview first).",
                      lambda e: open_structures_dialog(), ai=True)
    # ═══ EDITOR: colour column ═══════════════════════════════════════════════════════════════════════════════════
    # The editor cannot colour single words, so the same lyrics are drawn again in a column beside it (read-only, ordinary Text spans: rhyme colours). The two do not share a scroll bar (the editor
    # reports no scroll position); instead the column follows the CARET: moving the cursor in the editor scrolls the column to that line (highlighted), and a click on a line of the column puts the
    # caret on that line of the editor. The column is redrawn shortly after typing stops, and only when the lyrics or the rhyme result changed.
    cc = {"on": False, "rows": {}, "line": 0, "sig": None, "task": False, "due": 0.0, "sug": False, "more": set(), "view": "rhyme", "rev": 0, "style": "rhyme"}            # "sug": suggestions under the lines: off (the colour column is for something else now)
    cc_list = ft.Column([], spacing=0, scroll=ft.ScrollMode.AUTO, expand=True)       # a Column, not a ListView: scroll_to(scroll_key) does not work on the lazily built items of a ListView
    cc_title = ft.Text("Rhyme & Syllables", size=11, weight=ft.FontWeight.BOLD, color=C["text3"])
    cc_chips = ft.Row([], spacing=6)
    cc_info = ft.Text("", size=11, color=C["text3"])
    cc_rhythm_bar = ft.Row([cc_chips], spacing=8, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER, visible=False)
    cc_sug_btn = ibtn(ft.Icons.LIGHTBULB_OUTLINE_ROUNDED, "Show the AI suggestions under their lines (Apply or Ignore them here)", lambda e: cc_toggle_sug(), 16, color=C["text3"], w=26)
    cc_sug_btn.visible = False
    LD_COL = {"live": "#34D399", "dead": "#FB923C", "unsure": "#94A3B8"}                       # light green = live, orange = dead, grey = not sure
    cc_style_btns = {k: ft.Container(ft.Text(lbl, size=11, weight=ft.FontWeight.W_600), padding=po(10, 3, 10, 3), border_radius=12, ink=True, on_click=lambda e, k=k: cc_set_style(k))
                     for k, lbl in (("rhyme", "Rhyme"), ("ld", "Live / Dead"))}
    cc_style_row = ft.Row([cc_style_btns["rhyme"], cc_style_btns["ld"]], spacing=6, visible=True)
    for _k, _b in cc_style_btns.items():                                                      # the first paint (Rhyme is the open view)
        _b.bgcolor = ft.Colors.with_opacity(0.30, C["accent"]) if _k == "rhyme" else ft.Colors.with_opacity(0.06, "#FFFFFF")
        _b.content.color = C["text1"] if _k == "rhyme" else C["text3"]
    cc_legend = ft.Row([ft.Container(ft.Text(lbl, size=10, color="#E7EEFA"), bgcolor=ft.Colors.with_opacity(0.30, LD_COL[k]), border_radius=6, padding=po(6, 1, 6, 1)) for k, lbl in
                        (("live", "Live syllable"), ("dead", "Dead syllable"), ("unsure", "Not sure"))], spacing=6, wrap=True, visible=False)
    cc_panel = ft.Container(ft.Column([ft.Row([cc_sug_btn], spacing=2, wrap=True), cc_title, cc_style_row, cc_legend, cc_rhythm_bar, cc_list], spacing=4, expand=True), expand=True, visible=True, bgcolor="#00000000",
                            border_radius=8, padding=po(0, 0, 4, 0))

    def cc_line_of_caret() -> int:
        a = S.sel[0] if S.song else 0
        return lyr().count(chr(10), 0, max(0, a)) + 1

    def cc_go_line(n: int):
        """The caret goes to the start of line n of the editor (a click on that line of the column)."""
        if not S.song:
            return
        lines = lyr().split(chr(10))
        pos = sum(len(x) + 1 for x in lines[:max(0, n - 1)])
        S.sel = (pos, pos)
        show_lyrics_to_ui(select=S.sel, focus=True)

    def song_lang() -> str:
        """The LYRICS language of the open song (Song Settings), never the language of the window. "" while it is Auto: each word is then read by its own script."""
        v = (S.song.settings.language or "").strip() if S.song else ""
        return "" if v.lower() == "auto" else v

    def cc_current_result():
        sess = sessions.get(S.song.id) if S.song else None
        info = sess.rhyme if sess else None
        fresh = bool(info and "result" in info and info["snap"].text_hash == LA.text_hash(lyr()) and info["snap"].prompt_version == LR.RHYME_PROMPT_VERSION)
        return info["result"] if fresh and not info.get("partial") else None          # a result that is still being built is never used as a finished one

    CC_SPEC_COL = {"songwriter": "#2563EB", "music_producer": "#7C3AED", "song_architect": "#7C3AED", "music_director": "#0D9488"}
    CC_PRIO = {"high": 0, "medium": 1, "low": 2}

    def dr_cue_findings() -> list:
        """Section cues and line vocal cues of the CURRENT sheet's analysis. Each one is a suggestion. Nothing is written until the user accepts it."""
        if dr["prop"] is None or dr["snap"] is None or not S.song or (dr["snap"].song_id, dr["snap"].sheet_id) != (S.song.id, S.song.book.active_id):
            return []
        out = [SE.Finding(id=f"cue{i}", spec="music_director", kind="cue", priority="medium", first=c.line, last=c.line, evidence=c.header, finding=f"{c.section}: {c.cue}", reason=c.why, suggestion=c.new_header)
               for i, c in enumerate(dr["prop"].cues) if c.line and c.new_header and c.new_header != c.header]
        for i, c in enumerate(getattr(dr["prop"], "line_cues", None) or []):
            if c.line and c.suggestion and c.evidence:
                out.append(SE.Finding(id=f"ln{i}", spec="music_director", kind="line_vocal", priority="medium", first=c.line, last=c.line, evidence=c.evidence,
                                      finding=f"Line vocal: {c.cue}", reason=c.why, suggestion=c.suggestion))
        return out

    def cc_suggestions(text: str, force: bool = False) -> dict:
        """line now -> [(finding, shown text, option, last line)]: the OPEN suggestions of the report whose quoted text is found in the CURRENT lyrics (so a line is never marked wrongly after the lyrics moved).
        The same finding of both specialists is one entry (also_by); different suggestions for the same lines are separate entries."""
        out: dict = {}
        sess = sessions.get(S.song.id) if S.song else None
        if not (cc["sug"] or force) or sess is None:
            return out
        for f in _fd_all(sess) + dr_cue_findings():
            if not (f.suggestion and f.evidence and f.first) or _fd_state(f, sess) != "open":
                continue
            opts = f.options if len(f.options) > 1 else []
            opt = opts[min(fd_view["pick"].get(_fd_key(f), 0), len(opts) - 1)] if opts else None
            loc = SE.locate_text(_fd_item(f, opt), text)
            if not isinstance(loc, tuple):
                continue
            a, b = loc
            out.setdefault(text.count(chr(10), 0, a) + 1, []).append((f, opt if opt is not None else f.suggestion, opt, text.count(chr(10), 0, b) + 1))
        for v in out.values():
            v.sort(key=lambda t: CC_PRIO.get(t[0].priority, 1))
        return out

    def cc_sug_row(f, shown: str, opt, n0: int, n1: int):
        chips = [ft.Container(ft.Text(SP.name_of(x), size=9, weight=ft.FontWeight.W_600, color="#FFFFFF"), bgcolor=CC_SPEC_COL.get(x, C["text3"]), border_radius=6, padding=po(5, 0, 5, 1))
                 for x in [f.spec] + list(f.also_by)]
        if f.conflicts:
            chips.append(ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, size=13, color=C["amber"], tooltip="Another specialist suggests something different for these lines. Applying one removes the other."))
        if n1 > n0:
            chips.append(ft.Text(f"Lines {n0}-{n1}", size=9, color=C["text3"]))
        return ft.Container(ft.Row([ft.Column([ft.Row(chips, spacing=3, wrap=True, run_spacing=2), ft.Text(shown, size=12, color="#4ADE80", weight=ft.FontWeight.W_500)], spacing=1, expand=True),
                                    ft.IconButton(ft.Icons.CHECK_ROUNDED, icon_size=16, width=26, height=26, padding=0, icon_color="#4ADE80", tooltip="Apply", on_click=lambda e, g=f, o=opt: fd_apply(g, o)),
                                    ft.IconButton(ft.Icons.CLOSE_ROUNDED, icon_size=16, width=26, height=26, padding=0, icon_color=C["text3"], tooltip="Ignore", on_click=lambda e, g=f: fd_ignore(g))],
                                   spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            margin=po(26, 0, 2, 3), padding=po(6, 3, 2, 3), border_radius=6, bgcolor=ft.Colors.with_opacity(0.10, "#16A34A"), border=ft.Border.all(1, ft.Colors.with_opacity(0.25, "#16A34A")))

    def cc_toggle_sug():
        cc["sug"] = not cc["sug"]
        cc_sug_btn.icon_color = C["accent"] if cc["sug"] else C["text3"]
        safe_update(cc_sug_btn)
        cc_refresh(True)

    def cc_more(n: int):
        cc["more"] ^= {n}
        cc_refresh(True)

    # ── Live Lyrics Insights (Python only, never an LLM): the colour column has two views. 1) Rhyme & Syllables: the lyrics with the rhyme colours and "words / syllables" at the end of every line.
    # 2) Rhythm & Duration: vocal density and the song duration as graphs. The numbers come from live_insights.analyze / timing. The editor itself cannot colour single words or show text in its
    # gutter (flet-code-editor has only fixed syntax highlighting, line numbers and an icon per line), so this stays a read-only view beside it. ──────────────────────────────────────
    LI_MARK = {"manual_corrected": "✎ ", "estimated": "~", "unverified": "? ", "verified": ""}
    LI_LEVEL_COL = {"comfortable": "#2563EB", "moderate": "#7C3AED", "dense": "#F59E0B", "very_dense": "#EA580C", "unknown": "#94A3B8"}
    LI_CHIP = {"comfortable": ("#143D2B", "#6EE7B7"), "moderate": ("#2A2350", "#C4B5FD"), "dense": ("#4A3410", "#FCD34D"), "very_dense": ("#4A2410", "#FDBA74"), "unknown": ("#1E2A44", "#94A3B8")}
    LI_LINE, LI_CARD = C["border"], C["panel2"]
    LI_ON = {"comfortable": "#FFFFFF", "moderate": "#FFFFFF", "dense": "#1F2937", "very_dense": "#FFFFFF", "unknown": "#FFFFFF"}      # no red: a long line is not an error
    li_analyze = LI.analyze                                    # replaced by a timed version under MS_PERF=1
    li = {"ins": None, "text": None, "key": None, "ms": 0.0, "n": 0, "open": set()}

    threading.Thread(target=lambda: LI.analyze("[Verse]" + chr(10) + "ฝนตกหนักคน"), daemon=True, name="live-insights-warmup").start()          # loads the Thai dictionary once, off the UI thread, so the first opening of the column is not slow

    def li_corrections() -> dict:
        sess = sessions.get(S.song.id) if S.song else None
        return dict(sess.an_corr) if sess else {}

    def li_key() -> tuple:
        """Everything the analysis of a text depends on besides the text: the song, the sheet, the lyrics language and the user's syllable corrections. A result is reused only when all of it matches."""
        corr = tuple(sorted((str(k), getattr(c, "count", None), tuple(getattr(c, "units", None) or ())) for k, c in li_corrections().items()))
        return (S.song.id, S.song.book.active_id, song_lang(), corr)

    def li_cached(text: str):
        """The analysis of exactly this text of this sheet in this language, or None. Never analyses."""
        if S.song is not None and li["ins"] is not None and li["text"] == text and li["key"] == li_key():
            return li["ins"]
        return None

    def li_get(text: str):
        """The analysis of exactly this text, from the cache. On a miss it returns None and asks the background scheduler for it (the UI shows a pending state and is redrawn when it arrives):
        nothing is analysed on the UI thread, and a result of another song / sheet / language is never returned."""
        ins = li_cached(text)
        if ins is None:
            _perf_write("li_miss", 0.0)
            cc_request(0.0)
        else:
            _perf_write("li_hit", 0.0)
        return ins

    def li_settings() -> "LI.DensitySettings":
        """BPM, time signature and target duration are the song's own (Song Settings); nothing is chosen here: the density follows these settings and the lyrics."""
        s = S.song.settings
        return LI.DensitySettings(bpm=float(s.bpm or 120), timesig=str(s.timesignature or "4"), duration=float(s.duration or 180))

    def li_style(sp):
        col = LI.PALETTE[sp.color % len(LI.PALETTE)]
        if sp.kind == "near":
            return ft.TextStyle(color=col, italic=True, decoration=ft.TextDecoration.UNDERLINE, decoration_color=col, decoration_style=ft.TextDecorationStyle.WAVY)
        return ft.TextStyle(color=col, weight=ft.FontWeight.BOLD, bgcolor=ft.Colors.with_opacity(0.18, col))

    def li_segments(text: str, ins) -> list:
        """[(file line, [(text, span or None)])]. A rhyme colour is drawn only on the exact characters the analyzer named."""
        return LI.paint_segments(text, ins)

    async def _li_ask_after(kind, first, last, label, warning, extra, topic):
        if not S.song:
            return
        text, key, corr, lang = lyr(), li_key(), li_corrections(), song_lang()
        try:
            ins = await asyncio.to_thread(li_analyze, text, corr, li["n"], lang)
        except Exception as ex:
            dbg_once("li_ask analysis", ex)
            return
        if S.song and li_key() == key and lyr() == text:
            li["ins"], li["text"], li["key"] = ins, text, key
            li_ask(kind, first, last, label, warning, extra, topic)

    def li_ask(kind: str, first: int, last: int, label: str, warning=None, extra=None, topic: str = ""):
        """Ask AI about a section / line: the question is written into the chat box of the right specialist (nothing is sent; the user reads and edits it). Lyrics are never changed from here.
        With a density warning the message carries only: the section and its lyrics, the syllables, the density estimate, the reason and the settings the estimate used."""
        if not S.song:
            return
        text = lyr()
        ins = li_cached(text)
        if ins is None:                                           # not analysed yet: do it in a worker and come back (the click handler never waits for it)
            page.run_task(_li_ask_after, kind, first, last, label, warning, extra, topic)
            return
        dn = LI.density(ins, li_settings())
        lines = text.split(chr(10))
        part = chr(10).join(f"{i}| {lines[i - 1]}" for i in range(first, min(last, len(lines)) + 1))
        facts = [x for x in ins.lines if first <= x.line_number <= last]
        sec = next((s for s in dn.sections if s.name == label and s.first_line <= first <= max(s.last_line, s.first_line)), None)
        st = dn.settings
        used = f"BPM {st.bpm:g}, target duration {st.duration:g} s"
        th = I18N.lang() == "th"
        if extra is not None:                                     # a group of ACE-Step health notes: the notes, the settings and only the lyrics they point at (or the section list for a structure note)
            notes = chr(10).join("- " + x for x in extra)
            if kind == "songwriter":
                who, ask_txt = "songwriter", (f"ช่วยปรับ {label} ให้ร้องง่ายขึ้นและไม่แน่นเกินไป โดยรักษาความหมาย Hook และอารมณ์เดิมไว้" if th else f"Please adjust {label} so it is easier to sing and not too dense, keeping the meaning, the hook and the mood.")
                body = f"Lyrics:{chr(10)}{part}"
            else:
                who = "music_producer"
                if topic == "duplicate_section":
                    ask_txt = ("ชื่อท่อนนี้กลับมาอีกครั้ง ช่วยบอกว่าการซ้ำนี้ตั้งใจหรือไม่ และยังไม่ต้องแก้เนื้อเพลง" if th else
                               "This section name comes back later in the song. Please say whether that repeat is intentional. Do not rewrite the lyrics.")
                elif topic in ("cue_conflict", "tag_complexity", "vocal_cue"):
                    ask_txt = ("แท็กของท่อนนี้แน่นหรือคิวขัดกัน ช่วยเสนอแท็กที่สั้นลง และยังไม่ต้องแก้เนื้อเพลง" if th else
                               "The tag on this section is crowded or its cues disagree. Please suggest a simpler tag. Do not rewrite the lyrics.")
                else:
                    ask_txt = ("โครงสร้างเพลงนี้อาจแน่นเกินความยาวที่ตั้งไว้ ช่วยแนะนำการจัดโครงสร้างหรือการเรียบเรียง โดยไม่ต้องอ้างว่าควบคุมการร้องได้แน่นอน" if th else
                               "This structure may be too much for the target duration. Please suggest a leaner structure or arrangement, without claiming to control the singing.")
                body = "Sections: " + "; ".join(f"{x.name}{' #' + str(x.index + 1) if x.index else ''} ({x.sung_lines} lines, {x.syllables} syllables)" for x in dn.sections if not x.instrumental)
            set_spec(who)
            pick_ai_tab("chat")
            chat_tf.value = f"About {label} (ACE-Step lyrics health, estimates and guidelines, not recordings):{chr(10)}{notes}{chr(10)}Settings used: {used}.{chr(10)}{body}{chr(10)}{chr(10)}My question: {ask_txt}"
            chat_tf.max_lines = 10
            safe_update(chat_tf)
            try:
                page.run_task(chat_tf.focus)
            except Exception:
                pass
            return
        if kind == "songwriter":
            if warning is not None or (sec is not None and sec.level in ("dense", "very_dense")):
                why = warning.reason if warning is not None else (sec.reason if sec is not None else "")
                row = f"{label}: {sum(x.syllable_count for x in facts)} syllables in {len(facts)} lines; density estimate: {LI.LEVEL_NAME[(warning.level if warning is not None else sec.level)]}. {why} Settings used: {used}."
                ask_txt = (f"ช่วยปรับ {label} ให้คำร้องไม่แน่นเกินไป โดยรักษาความหมาย Hook และอารมณ์เดิมไว้" if th else
                           f"Please adjust {label} so the lyrics are not too dense, keeping the meaning, the hook and the mood.")
            else:
                row = chr(10).join(f"line {x.line_number}: {x.word_count} words / {x.syllable_count} syllables ({x.status.replace('_', ' ')}); rhyme: " + (", ".join(f"{sp.text}={sp.group}" for sp in x.rhyme_spans) or x.rhyme_status or "-") for x in facts)
                ask_txt = "Make this easier to sing but keep the hook. "
            who = "songwriter"
        else:
            row = (f"{label}: {sec.syllables} syllables, density estimate {LI.LEVEL_NAME[sec.level]}. " if sec is not None and not sec.instrumental else "") + f"Settings used: {used}."
            who, ask_txt = "music_producer", "How should the arrangement and the vocal delivery support this section? "
        set_spec(who)
        pick_ai_tab("chat")
        chat_tf.value = f"About {label or 'this part'} (lines {first}-{last}). Program facts (estimates, not recordings):{chr(10)}{row}{chr(10)}Lyrics:{chr(10)}{part}{chr(10)}{chr(10)}My question: {ask_txt}"
        chat_tf.max_lines = 10
        safe_update(chat_tf)
        try:
            page.run_task(chat_tf.focus)
        except Exception:
            pass

    def li_ask_menu(first: int, last: int, label: str):
        return ft.PopupMenuButton(content=ft.Container(ft.Icon(ft.Icons.AUTO_AWESOME_OUTLINED, size=14, color=C["text3"]), width=22, height=18, alignment=ft.Alignment(0, 0)), padding=0, tooltip="Ask AI about this part (the question is written in the chat box; you edit it before sending)",
                                  items=[ft.PopupMenuItem(content=ft.Text("Ask the Songwriter AI (words, rhyme, flow)"), on_click=lambda e: li_ask("songwriter", first, last, label)),
                                         ft.PopupMenuItem(content=ft.Text("Ask the Music Producer AI (arrangement, delivery)"), on_click=lambda e: li_ask("producer", first, last, label))])

    def cc_sig():
        if not S.song:
            return None
        sess = sessions.get(S.song.id)
        st = S.song.settings
        return (S.song.id, LA.text_hash(lyr()), cc["sug"], tuple(sorted(sess.fd_status.items())) if sess else (), tuple(sorted(cc["more"])), tuple(sorted(fd_view["pick"].items())),
                len(sess.specreports) if sess else 0, cc["view"], cc["style"], st.bpm, st.timesignature, st.duration, tuple(sorted(map(str, li["open"]))),
                li_key()[2:])

    def li_counter(x):
        tip = (f"{x.word_count} words · {x.syllable_count} syllables" + chr(10) +
               {"verified": "Thai dictionary split", "estimated": "Estimated (an English word, a number or a word not in the dictionary)", "manual_corrected": "Your own correction",
                "unverified": "Could not be counted"}[x.status] + (chr(10) + chr(10).join(x.warnings) if x.warnings else ""))
        return ft.Text(f"{LI_MARK[x.status]}{x.word_count} / {x.syllable_count}", size=10, color=C["text3"], width=54, text_align=ft.TextAlign.RIGHT, tooltip=tip)

    def ld_counter(row):
        """"dead 3 / 7": the dead syllables of the line out of the syllables that were classified (live + dead + not sure). Words that are not Thai are not counted."""
        tip = (f"{I18N.tr('Live syllable')}: {row.live} · {I18N.tr('Dead syllable')}: {row.dead} · {I18N.tr('Not sure')}: {row.unsure}" + (f" · {I18N.tr('not Thai')}: {row.other}" if row.other else ""))
        return ft.Text(f"{I18N.tr('dead')} {row.dead}/{row.total}" if row.total else "", size=10, color=C["text3"], width=62, text_align=ft.TextAlign.RIGHT, tooltip=tip)

    def cc_rebuild_ld(text: str, ins):
        """The live / dead view: the lyrics as written, every classified syllable on a tint (light green live, orange dead, grey not sure). Nothing here changes the rhyme colours or the lyrics."""
        res = LD.analyze(text, ins)
        secs = {s.first_line: s for s in res.sections}
        rows = []
        if res.reader_warning:                                  # an old pythainlp misreads vowels: said once, above the lines (nothing is corrected silently)
            rows.append(ft.Container(ft.Text(res.reader_warning, size=11, color=C["amber"], selectable=True), padding=po(4, 4, 4, 6)))
        for n, segs in LD.paint_segments(text, res):
            if not segs:
                row = ft.Container(height=12, data=n)
            else:
                tag = n in secs
                spans = [ft.TextSpan(t, style=(ft.TextStyle(color=C["text1"], bgcolor=ft.Colors.with_opacity(0.26 if sy.cls != "unsure" else 0.20, LD_COL[sy.cls])) if sy else ft.TextStyle(color=C["text3"] if tag else C["text1"])))
                         for t, sy in segs]
                end = []
                if tag:
                    s_ = secs[n]
                    end = [ft.Text(f"{I18N.tr('dead')} {s_.dead}/{s_.total}" if s_.total else "", size=10, color=C["text3"], width=62, text_align=ft.TextAlign.RIGHT,
                                   tooltip=f"{I18N.tr('Live syllable')}: {s_.live} · {I18N.tr('Dead syllable')}: {s_.dead} · {I18N.tr('Not sure')}: {s_.unsure}")]
                elif n in res.lines:
                    end = [ld_counter(res.lines[n])]
                row = ft.Container(ft.Row([ft.Text(str(n), size=10, color=C["text3"], width=22), ft.Text(spans=spans, size=13, expand=True)] + end, spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
                                   padding=po(4, 2, 4, 2), border_radius=4, ink=True, on_click=lambda e, n=n: cc_go_line(n), data=n)
            row.key = ft.ScrollKey(f"cc{n}")
            cc["rows"][n] = row
            rows.append(row)
        cc_title.value = f"{I18N.tr('Live / Dead')} · {len(ins.lines)} {I18N.tr('lines')} · {I18N.tr('dead')} {res.dead}/{res.total}" + (f" · {res.unsure} {I18N.tr('Not sure')}" if res.unsure else "")
        return rows

    def cc_rebuild_rhyme(text: str, ins):
        if cc["style"] == "ld":
            return cc_rebuild_ld(text, ins)
        by_n = {x.line_number: x for x in ins.lines}
        secs = {s.first_line: s for s in ins.sections}
        rows, lines_all = [], text.split(chr(10))
        for n, segs in li_segments(text, ins):
            if not segs:
                row = ft.Container(height=12, data=n)
            else:
                tag = n in secs
                x = by_n.get(n)
                spans = [ft.TextSpan(t, style=li_style(sp) if sp else ft.TextStyle(color=C["text3"] if tag else C["text1"])) for t, sp in segs]
                end = [li_counter(x)] if x else []
                if tag:
                    s = secs[n]
                    last = max([y.line_number for y in ins.lines if y.sec_index == ins.sections.index(s)] or [n])
                    end = [ft.Text(f"{s.syllables} syl" if s.sung_lines else "", size=10, color=C["text3"], width=54, text_align=ft.TextAlign.RIGHT, tooltip=f"{s.sung_lines} lines · {s.words} words · {s.syllables} syllables"
                                   + (chr(10) + chr(10).join(s.warnings) if s.warnings else "")), li_ask_menu(n, last, s.name)]
                row = ft.Container(ft.Row([ft.Text(str(n), size=10, color=C["text3"], width=22), ft.Text(spans=spans, size=13, expand=True)] + end, spacing=6, vertical_alignment=ft.CrossAxisAlignment.START),
                                   padding=po(4, 2, 4, 2), border_radius=4, ink=True, on_click=lambda e, n=n: cc_go_line(n), data=n)
            row.key = ft.ScrollKey(f"cc{n}")
            cc["rows"][n] = row
            rows.append(row)
        cc_title.value = f"Rhyme & Syllables · {len(ins.lines)} lines" + (f" · {sum(1 for x in ins.lines if x.status in ('estimated', 'unverified'))} estimated" if any(x.status in ("estimated", "unverified") for x in ins.lines) else "")
        return rows

    def li_bar(text: str, tip: str, frac: float, col: str, n: int):
        return ft.Container(ft.Row([ft.Container(width=max(2.0, 150 * frac), height=8, bgcolor=col, border_radius=3, tooltip=tip)], spacing=0), padding=po(0, 1, 0, 1), ink=True, on_click=lambda e, n=n: cc_go_line(n), data=n)

    def li_goto(n: int, h: str):
        """Jump to a line only while the lyrics are still exactly the ones the warning was made for: never to a line that moved."""
        if LA.text_hash(lyr()) != h:
            toast("The lyrics changed since this warning was made. It is updating: nothing was moved.", C["amber"]); return
        cc_go_line(n)

    def li_health_card(group: list, h: str):
        """One card for all the notes of one section (or the whole song / the structure). Click: details, the basis of each note, and the buttons. No popup."""
        first = group[0]
        if first.kind == "song_density":
            key = "song"
        elif first.kind in LI.STRUCTURE_KINDS:
            key = f"{first.kind}:{first.section}#{first.index}:{first.first_line}"
        else:
            key = f"{first.section}#{first.index}"
        opened = key in li["open"]
        tone = LI.health_tone(group)
        col = LI_LEVEL_COL["very_dense"] if tone == "warning" and min(x.priority for x in group) <= 2 else LI_LEVEL_COL["dense"] if tone == "warning" else LI_LEVEL_COL["moderate"]
        title = first.title if len(group) == 1 else f"{first.section}{' #' + str(first.index + 1) if first.index else ''} — {len(group)} notes"
        body = [ft.Row([ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED if tone == "warning" else ft.Icons.INFO_OUTLINE_ROUNDED, size=14, color=col),
                        ft.Text(title, size=11, weight=ft.FontWeight.BOLD, color=C["text1"], expand=True),
                        ft.Icon(ft.Icons.EXPAND_LESS_ROUNDED if opened else ft.Icons.EXPAND_MORE_ROUNDED, size=14, color=C["text3"])], spacing=6)]
        for x in (group if opened else group[:1]):
            body.append(ft.Text(x.summary, size=10, color=C["text3"]))
            if opened:
                body.append(ft.Text(LI.BASIS_NAME[x.basis] + (" (estimated count)" if x.hedged else ""), size=10, weight=ft.FontWeight.BOLD, color=C["text1"]))
                body.append(ft.Text(x.detail, size=10, italic=True, color=C["text3"]))
        if not opened and len(group) > 1:
            body.append(ft.Text(f"+{len(group) - 1} more (click to open)", size=10, color=C["accent"]))
        if opened:
            lines_ = sorted({n for x in group for n in x.lines})
            label = "the whole song" if first.kind == "song_density" else ("the structure" if first.kind == "structure" else (first.section or "the structure"))
            summaries = [x.summary for x in group]
            btns = []
            if lines_:
                btns.append(ft.TextButton("Go to line", icon=ft.Icons.MY_LOCATION_OUTLINED, on_click=lambda e, n=lines_[0]: li_goto(n, h), style=ft.ButtonStyle(padding=po(4, 0, 4, 0), color=C["accent"])))
            if any(x.spec == "songwriter" for x in group):
                a, b = (lines_[0], lines_[-1]) if lines_ else (1, len(lyr().split(chr(10))))
                btns.append(ft.TextButton("Ask Songwriter", icon=ft.Icons.AUTO_AWESOME_OUTLINED, tooltip="Write a question in the chat box (you edit it before sending; nothing is sent by itself)",
                                          on_click=lambda e, a=a, b=b, lb=label, sm=summaries: li_ask("songwriter", a, b, lb, None, sm), style=ft.ButtonStyle(padding=po(4, 0, 4, 0), color=C["accent"])))
            if any(x.spec == "music_producer" for x in group) or first.kind == "song_density":
                btns.append(ft.TextButton("Ask Music Producer", icon=ft.Icons.AUTO_AWESOME_OUTLINED, tooltip="Write a question in the chat box (you edit it before sending; nothing is sent by itself)",
                                          on_click=lambda e, lb=label, sm=summaries, tp=first.kind: li_ask("producer", 1, len(lyr().split(chr(10))), lb, None, sm, tp), style=ft.ButtonStyle(padding=po(4, 0, 4, 0), color=C["accent"])))
            body.append(ft.Row(btns, spacing=2, wrap=True))
        return ft.Container(ft.Column(body, spacing=2), padding=po(6, 4, 6, 4), border_radius=6, ink=True, bgcolor=ft.Colors.with_opacity(0.06, col),
                            border=ft.Border.all(1, ft.Colors.with_opacity(0.35, col)), on_click=lambda e, k=key: (li["open"].discard(k) if k in li["open"] else li["open"].add(k), cc_refresh(force=True)))

    LI_SEC_ICON = (("intro", ft.Icons.PLAY_ARROW_ROUNDED), ("verse", ft.Icons.PLAY_ARROW_OUTLINED), ("pre-chorus", ft.Icons.TRENDING_UP_ROUNDED), ("chorus", ft.Icons.STAR_OUTLINE_ROUNDED),
                   ("build", ft.Icons.SIGNAL_CELLULAR_ALT_ROUNDED), ("drop", ft.Icons.BOLT_ROUNDED), ("breakdown", ft.Icons.LAYERS_OUTLINED), ("bridge", ft.Icons.SWAP_HORIZ_ROUNDED),
                   ("outro", ft.Icons.FLAG_OUTLINED), ("instrumental", ft.Icons.PIANO_OUTLINED), ("solo", ft.Icons.MUSIC_NOTE_ROUNDED))
    LI_LEVEL_ICON = {"comfortable": ft.Icons.CHECK_CIRCLE_ROUNDED, "moderate": ft.Icons.INFO_ROUNDED, "dense": ft.Icons.WARNING_AMBER_ROUNDED, "very_dense": ft.Icons.PRIORITY_HIGH_ROUNDED,
                     "unknown": ft.Icons.HELP_OUTLINE_ROUNDED}

    def li_sec_icon(name: str):
        n = (name or "").lower()
        return next((ic for k, ic in LI_SEC_ICON if n.startswith(k)), ft.Icons.MUSIC_NOTE_OUTLINED)

    def li_chip(level: str, width: "int | None" = None, label: str = ""):
        bg, fg = LI_CHIP[level]
        return ft.Container(ft.Row([ft.Icon(LI_LEVEL_ICON[level], size=13, color=fg), ft.Text(label or LI.LEVEL_NAME[level], size=10, weight=ft.FontWeight.BOLD, color=fg)], spacing=3, tight=True,
                                   alignment=ft.MainAxisAlignment.CENTER), bgcolor=bg, border_radius=12, padding=po(8, 3, 8, 3), width=width)

    LI_STATUS_LEVEL = {"ok": "comfortable", "warning": "dense", "high": "very_dense", "unknown": "unknown"}

    def li_status_chip(status: str, width: "int | None" = None):
        return li_chip(LI_STATUS_LEVEL.get(status, "unknown"), width, LI.STATUS_NAME.get(status, "Unknown"))

    def li_track(frac: float, col: str, tip: str = ""):
        """A bar on a visible track: the filled share is `frac` of the width (the track takes the width the row leaves)."""
        a = max(2, min(100, int(round(frac * 100))))
        parts = [ft.Container(bgcolor=col, expand=a)] + ([ft.Container(expand=100 - a)] if a < 100 else [])
        return ft.Container(ft.Row(parts, spacing=0), height=8, bgcolor=LI_LINE, border_radius=4, expand=True, clip_behavior=ft.ClipBehavior.ANTI_ALIAS, tooltip=tip or None)

    def cc_rebuild_rhythm(text: str, ins):
        """Lyrics Density & Vocal Risk: a card with one tidy row per section (name and size, a bar on a track, the level), the ACE-Step notes and the assumptions. Numbers come from LI.density and LI.health:
        an estimate, no melody is known. Nothing pops up while typing."""
        ts = li_settings()
        dn = LI.density(ins, ts)
        T1, T2, T3 = C["text1"], C["text3"], "#94A3B8"
        cc_info.value = ""
        cc_chips.controls = [ft.Container(ft.Row([ft.Icon(ft.Icons.MUSIC_NOTE_ROUNDED, size=13, color=T2), ft.Text(f"BPM {ts.bpm:g}", size=11, color=T1),
                                                  ft.Container(width=1, height=14, bgcolor=LI_LINE), ft.Icon(ft.Icons.VIEW_WEEK_OUTLINED, size=13, color=T2), ft.Text(LI.pulses_per_bar(ts.timesig)[1].split(" ")[0], size=11, color=T1),
                                                  ft.Container(width=1, height=14, bgcolor=LI_LINE), ft.Icon(ft.Icons.TIMER_OUTLINED, size=13, color=T2), ft.Text(f"Target {ts.duration:g} s", size=11, color=T1)],
                                                 spacing=6, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER), border_radius=14, padding=po(10, 5, 10, 5), bgcolor=C["panel2"])]
        card_rows = [ft.Row([ft.Container(ft.Icon(ft.Icons.BAR_CHART_ROUNDED, size=18, color="#2563EB"), bgcolor=C["accent_soft"], border_radius=8, padding=6),
                             ft.Text("Lyrics density & vocal risk", size=13, weight=ft.FontWeight.BOLD, color=T1, expand=True)], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)]
        # two separate states: how the song fits the target duration (the whole song), and how many lines are risky (from the lines, never from an average)
        fit_status = {"fits": "ok", "long": "high", "short": "warning", "unknown": "unknown"}[dn.fit]
        fit_label = {"fits": "Fits", "long": "Too long", "short": "Too short", "unknown": "Unknown"}[dn.fit]
        fit_text = (f"the lyrics and instrumental parts take about {dn.need_low:.0f}-{dn.need_high:.0f} s (a range, lines are not equal) against the target {ts.duration:g} s" if dn.fit != "unknown"
                    else "needs the BPM and the target duration, and some sung lines")
        card_rows.append(ft.Column([ft.Row([ft.Text("Song Duration Fit", size=11, weight=ft.FontWeight.W_600, color=T1, expand=True), li_chip(LI_STATUS_LEVEL[fit_status], None, fit_label)], spacing=8,
                                            vertical_alignment=ft.CrossAxisAlignment.CENTER), ft.Text(fit_text, size=10, color=T2)], spacing=2))
        n_risk = len(dn.risky_lines)
        dens_text = (dn.why_unknown if not dn.known and not dn.bpm_known else
                     (f"{n_risk} of {dn.total_lines} lines to check" + (": lines " + ", ".join(str(n) for n in dn.risky_lines[:6]) + (" …" if n_risk > 6 else "") if n_risk else "")))
        card_rows.append(ft.Column([ft.Row([ft.Text("Vocal Density", size=11, weight=ft.FontWeight.W_600, color=T1, expand=True), li_status_chip(dn.status)], spacing=8,
                                            vertical_alignment=ft.CrossAxisAlignment.CENTER), ft.Text(dens_text, size=10, color=T2)], spacing=2))
        if not dn.known and dn.bpm_known:
            card_rows.append(ft.Text(dn.why_unknown, size=10, color=T2))
            card_rows.append(ft.Text("Only phrasing and the longest-phrase case can be judged.", size=10, color=T2))
        by_line = {x.line_number: x for x in dn.lines}
        by_ins = {x.line_number: x for x in ins.lines}
        warn_by = {(w.section, w.index): w for w in dn.warnings}
        first_row = True
        for sd in dn.sections:
            if sd.instrumental or not sd.sung_lines:
                continue
            name = f"{sd.name}{' #' + str(sd.index + 1) if sd.index else ''}"
            w = warn_by.get((sd.name, sd.index))
            lines_key = f"lines:{sd.name}#{sd.index}"
            lines_open = (bool(sd.risky_lines)) != (lines_key in li["open"])          # a section with a line to check starts open, the others closed; the arrow turns it the other way
            n_r = len(sd.risky_lines)
            sec_note = (f"{n_r} line{'s' if n_r != 1 else ''} to check" + (f", especially line {sd.worst_line}" if n_r and sd.worst_line else "")) if n_r else (
                "no line to check" if sd.status == "ok" else "not enough data to judge")
            head = ft.Row([ft.Icon(li_sec_icon(sd.name), size=16, color=T2),
                           ft.Column([ft.Text(name, size=12, weight=ft.FontWeight.W_600, color=T1), ft.Text(f"{sd.sung_lines} lines · {sd.syllables} syl", size=10, color=T3), ft.Text(sec_note, size=10, color=T2)], spacing=0, tight=True, expand=True),
                           ibtn(ft.Icons.EXPAND_LESS_ROUNDED if lines_open else ft.Icons.EXPAND_MORE_ROUNDED, "Show or hide every line of this section", lambda e, k=lines_key: (li["open"].discard(k) if k in li["open"] else li["open"].add(k), cc_refresh(force=True)), 16, color=T3, w=26),
                           li_status_chip(sd.status, 92)], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            body = [ft.Container(head, ink=True, on_click=lambda e, n=(sd.worst_line or sd.first_line): cc_go_line(n), tooltip=(sd.reason + chr(10) if sd.reason else "") + "Estimated risk, not a measurement: the melody is not known.")]
            if w is not None:
                body.append(ft.Row([ft.Container(width=24), ft.TextButton("Ask Songwriter", icon=ft.Icons.AUTO_AWESOME_OUTLINED, tooltip="Write a question about this section in the chat box (you edit it before sending; nothing is sent by itself)",
                                                on_click=lambda e, w=w: li_ask("songwriter", w.first_line, w.last_line, w.section, w), style=ft.ButtonStyle(padding=po(4, 0, 4, 0), color=C["accent"]))], spacing=0))
            if lines_open:                                                 # one row per line: number, syllables, status, a short reason; a click goes to that line in the editor
                for n in range(sd.first_line + 1, sd.last_line + 1):
                    x, ld = by_ins.get(n), by_line.get(n)
                    if x is None or ld is None:
                        continue
                    est = "" if ld.confidence == "high" else "~"
                    ltip = (f"Line {n}: {x.text}{chr(10)}{x.word_count} words · {est}{x.syllable_count} syllables ({x.status.replace('_', ' ')}) · phrases {ld.phrases}{chr(10)}confidence: {ld.confidence}"
                            + (chr(10) + ld.detail if ld.detail else "") + chr(10) + "Estimated risk, not a measurement: the melody is not known.")
                    body.append(ft.Container(ft.Row([ft.Container(width=24), ft.Text(f"Line {n}", size=10, color=T2, width=44), ft.Text(f"{est}{x.syllable_count} syl", size=10, color=T3, width=44),
                                                     ft.Column([ft.Text(p, size=10, color=T2) for p in ld.reason.split("; ") if p], spacing=0, tight=True, expand=True), li_status_chip(ld.status, 92)], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                                             ink=True, tooltip=ltip, on_click=lambda e, n=n: cc_go_line(n)))
            card_rows.append(ft.Divider(height=1, color=LI_LINE))
            card_rows.append(ft.Container(ft.Column(body, spacing=2), padding=po(0, 2, 0, 2)))
        rows = [ft.Container(ft.Column(card_rows, spacing=6), border=ft.Border.all(1, LI_LINE), border_radius=12, padding=po(10, 8, 10, 8), bgcolor=LI_CARD)]
        items = LI.health(ins, dn)

        def health_rows(title: str, group_items: list):
            if not group_items:
                return
            rows.append(ft.Text(title, size=12, weight=ft.FontWeight.BOLD, color=T1))
            groups: dict = {}
            for h in group_items:
                if h.kind == "song_density":
                    gkey = "song"
                elif h.kind in LI.STRUCTURE_KINDS:
                    gkey = (h.kind, h.section, h.index, h.first_line)
                else:
                    gkey = (h.section, h.index)
                groups.setdefault(gkey, []).append(h)
            for g in sorted(groups.values(), key=lambda g: (min(x.priority for x in g), min(x.first_line for x in g))):
                rows.append(li_health_card(g, LA.text_hash(text)))

        if items:
            health_rows("Lyrics density", [h for h in items if LI.health_group(h.kind) == "density"])
            health_rows("Section structure", [h for h in items if LI.health_group(h.kind) == "structure"])
        elif dn.known:
            rows.append(ft.Text("No section, line or structure looks risky for these settings.", size=11, color=T2))
        rows.append(ft.Container(ft.Row([ft.Container(ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, size=16, color=C["accent"]), bgcolor=C["accent_soft"], border_radius=8, padding=6),
                                         ft.Column([ft.Text("Notes / assumptions", size=12, weight=ft.FontWeight.BOLD, color=T1), ft.Text(dn.assumptions, size=10, color=T2)], spacing=2, expand=True)],
                                        spacing=8, vertical_alignment=ft.CrossAxisAlignment.START), border=ft.Border.all(1, LI_LINE), border_radius=12, padding=po(10, 8, 10, 8), bgcolor=LI_CARD))
        cc_title.value = ""
        return rows

    def cc_rebuild():
        """All lines drawn again; a row per file line (rhyme view) so that line n is cc['rows'][n]."""
        text = lyr()
        ins = li_get(text)
        cc["rows"] = {}
        if ins is None:                                           # the analysis is in a worker: show that, and draw again when it arrives (the signature stays empty so the next refresh rebuilds)
            cc_list.controls = [ft.Container(ft.Row([ft.ProgressRing(width=14, height=14, stroke_width=2, color=C["accent"]), ft.Text("Analysing the lyrics…", size=11, color=C["text3"])], spacing=8,
                                                    vertical_alignment=ft.CrossAxisAlignment.CENTER), padding=po(10, 10, 10, 10))]
            cc["sig"] = None
            cc["line"] = 0
            return
        rows = cc_rebuild_rhyme(text, ins) if cc["view"] == "rhyme" else cc_rebuild_rhythm(text, ins)
        cc_list.controls = rows
        cc["sig"] = cc_sig()
        cc["line"] = 0
        cur = cc_line_of_caret()
        mark = cc["rows"].get(cur)
        if mark is not None and mark.content is not None:
            mark.bgcolor = ft.Colors.with_opacity(0.16, C["accent"])
        cc["line"] = cur

    def cc_style_paint():
        for k, b in cc_style_btns.items():
            on = cc["style"] == k
            b.bgcolor = ft.Colors.with_opacity(0.30, C["accent"]) if on else ft.Colors.with_opacity(0.06, "#FFFFFF")
            b.content.color = C["text1"] if on else C["text3"]
        cc_style_row.visible = cc["view"] == "rhyme"
        cc_legend.visible = cc["view"] == "rhyme" and cc["style"] == "ld"
        safe_update(cc_style_row, cc_legend)

    def cc_set_style(k: str):
        """Rhyme | Live / Dead: the same column, two views. The scroll position and the open sections are kept; neither view changes the other's analysis."""
        if cc["style"] == k:
            return
        cc["style"] = k
        cc_style_paint()
        cc_refresh(force=True)

    def cc_view(v: str):
        if cc["view"] == v:
            return
        cc["view"] = v
        dark = v == "rhythm"
        cc_title.visible = not dark
        cc_rhythm_bar.visible = dark
        cc_style_paint()
        safe_update(cc_panel, cc_title, cc_rhythm_bar)
        cc_refresh(force=True)

    async def cc_scroll_to(n: int):
        try:
            await cc_list.scroll_to(scroll_key=f"cc{n}", duration=120)
        except Exception as ex:
            log.debug("colour column scroll_to failed: %r", ex)

    def cc_follow():
        """Highlight the caret's line in the column and scroll it into view (only when the caret moved to another line)."""
        if not cc["on"] or not S.song:
            return
        n = cc_line_of_caret()
        if n == cc["line"] or n not in cc["rows"]:
            return
        old = cc["rows"].get(cc["line"])
        row = cc["rows"][n]
        try:
            if old is not None and isinstance(old, ft.Container):
                old.bgcolor = None
            row.bgcolor = ft.Colors.with_opacity(0.16, C["accent"]) if row.content is not None else None
        except RuntimeError:                                    # the rows were frozen by a re-render of the panel: draw the column again and mark the line on the new rows
            cc_rebuild()
            safe_update(cc_list, cc_title, cc_info, cc_chips)
            cc["line"] = n                                      # cc_rebuild already marked the caret line on the new rows (rows are not touched after they were sent)
            page.run_task(cc_scroll_to, n)
            return
        cc["line"] = n
        safe_update(*[x for x in (old, row) if x is not None])
        page.run_task(cc_scroll_to, n)

    def cc_refresh(force: bool = False):
        if not cc["on"] or not S.song:
            return
        sig = cc_sig()
        if force or sig != cc["sig"]:
            cc_rebuild()
            safe_update(cc_list, cc_title, cc_info, cc_chips)
        cc_follow()

    def _cc_snapshot():
        if not (cc["on"] and S.song):
            return None
        return LS.Snapshot(li_key(), lyr(), (li_corrections(), song_lang()))

    def _cc_analyze(snap):
        """Runs in a worker thread."""
        corr, lang = snap.payload
        cc["rev"] += 1
        t0 = time.perf_counter()
        ins = li_analyze(snap.text, corr, cc["rev"], lang)
        return ins, (time.perf_counter() - t0) * 1000, cc["rev"]

    def _cc_deliver(snap, res):
        ins, ms, rev = res
        li["ins"], li["text"], li["key"], li["ms"], li["n"] = ins, snap.text, snap.key, ms, rev
        log.debug("live insights: %d lines in %.0f ms", len(ins.lines), ms)
        _perf_write("li_analyze_done", ms)
        cc_refresh()

    _cc_sched = LS.LatestWins(_cc_snapshot, _cc_analyze, _cc_deliver, debounce=0.25, on_error=lambda ex: dbg_once("live insights analysis", ex))

    def cc_request(delay: "float | None" = None):
        """Ask for the analysis of the newest lyrics (after the typing pause). One worker at a time; if the text changes while it works, the newest text is analysed next, so the last result always matches the last text."""
        if not cc["on"]:
            return
        if _cc_sched.request(delay):
            try:
                page.run_task(_cc_sched.run)
            except Exception as ex:                                 # the worker could not be started: free the scheduler, so the next request can try again
                _cc_sched.release()
                dbg_once("live insights start", ex)

    def cc_tick():
        cc_request()

    def cc_sync():
        """The Python analysis (Rhyme & Syllables, Rhythm & Duration) is drawn only while its page of the right panel is open: it follows the open tab, and stops when the panel is closed or another
        tab is shown (nothing is analysed for a page nobody sees)."""
        want = "rhyme" if rp["tab"] == "cc_rhyme" else ("rhythm" if rp["tab"] == "cc_rhythm" else "")
        on = bool(want and (layout["side"] or layout["drawer"]) and S.song)
        was = cc["on"]
        cc["on"] = on
        if on:
            if cc["view"] != want:
                cc_view(want)                                       # a new view redraws itself
            elif not was:
                cc_refresh(force=True)                              # the page has just been opened
            else:
                cc_refresh()                                        # already open: only when the signature changed
    _hooks["caret"] = cc_follow
    fd_view["after"] = lambda: cc_refresh()
    dr_hooks.append(cc_tick)

    save_btn = tbtn(ft.Icons.SAVE_OUTLINED, "Save song", save_song_now)
    save_ui = {"d": False}

    def sync_save_btn():
        d = bool(dirty_txt.value)
        if save_ui["d"] != d:
            save_ui["d"] = d
            save_btn.icon_color = "#FFFFFF" if d else C["text2"]
            save_btn.style = tb_style("#4A86FF" if d else TB_HOVER, C["accent"] if d else "#00000000")
            safe_update(save_btn)


    async def work_watch():
        """A spinner beside the lyrics title whenever ANY AI job is running in the background (chat, analysis, review, AI actions, structure, previews), so the user never waits in silence."""
        on = None
        while True:
            try:
                sync_save_btn()
                rings = (act_ring, review_ring, an_ring, rh_ring, rt_ring, th_ring, pv_ring, tp_go_ring, chat_busy)
                now = bool(S.chat_busy_song or S.an_busy or dr["busy"] or tp["busy"] or pv["busy"] or rep["running"] or any(getattr(r, "visible", False) for r in rings))
                label = (f"Step {rep['step']}…" if rep["step"] else "AI is working…") if rep["running"] else "AI is working…"
                if now != on or work_txt.value != label:
                    on = now
                    work_ring.visible = work_txt.visible = now
                    work_txt.value = label
                    safe_update(work_ring, work_txt)
            except Exception as ex:
                dbg_once("work_watch", ex)
            await asyncio.sleep(0.4)

    lyrics_header = ft.Row([ft.Row([ft.Text("Lyrics", size=18, weight=ft.FontWeight.BOLD, color=C["text1"]), title_tf, dirty_txt], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            ft.Row([format_ring, tgroup(lyric_format_btn, struct_btn, ai=True), tsep(),
                                    tgroup(btn_tools, tbtn(ft.Icons.LABEL_OUTLINED, "Section Options… for the [tag] at the cursor (Ctrl+Shift+E)", open_section_options)), tsep(),
                                    tgroup(btn_undo, btn_redo), tsep(), tgroup(copy_menu, save_btn)], spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER)],
                           alignment=ft.MainAxisAlignment.SPACE_BETWEEN, wrap=True, run_spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    cc_ed = ft.Container(drop_target, expand=True)                       # the editor fills the whole centre panel

    lyrics_panel = panel(ft.Column([lyrics_header, sheet_bar, ft.Row([cc_ed], spacing=4, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH), ft.Row([ft.Container(notes_txt, expand=True), tag_chip, sel_chip], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)], spacing=8, expand=True,
                                  horizontal_alignment=ft.CrossAxisAlignment.STRETCH), padding=12, expand=True)
    top_row = ft.Row([lyrics_panel], spacing=8, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH)

    # Workspace (under the editor and the tools) ---------------------------------------------------------------------------------
    WS_TABS = [("lyrics", "Lyrics"), ("actions", "AI Actions"), ("tools", "Lyrics Tools")] + ([("preview", "Preview")] if PREVIEW_VISIBLE else []) + ([("timeline", "Timeline")] if TIMELINE_VISIBLE else [])
    WS_BODY = {"lyrics": ["Sheet summary", "Sections"], "preview": ["Section audio preview"],
               "actions": ["Continue Lyrics", "Increase Emotion", "Improve Singability", "Translate Lyrics", "Song Structures"],
               "timeline": ["Audio Timeline", "Section Markers", "Jump to Section", "Per-Take Timing (tied to the Take ID)"]}
    ws_tabs_row = ft.Row(spacing=4)
    ws_content = ft.Container(expand=True, padding=po(4, 4, 4, 4))

    def on_action_click(key: str):
        if key == "translate":
            open_translate_dialog()
        elif key == "phrasing":
            open_phrasing_dialog()
        else:
            start_action(key)

    def _action_card(key: str):
        spec = AC.AI_ACTIONS[key]
        return ft.Container(ft.Column([ft.Text(spec["label"], size=12, weight=ft.FontWeight.W_600, color=C["text1"], no_wrap=True), ft.Text(spec["sub"], size=10, color=C["text3"], no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)], spacing=1),
                            bgcolor=C["panel2"], border=ft.Border.all(1, C["border"]), border_radius=8, padding=po(10, 6, 10, 6), width=168, ink=True, tooltip=spec["tip"],
                            on_click=lambda e, k=key: on_action_click(k))
    action_cards = [_action_card(k) for k in ("continue", "emotion", "singability", "translate")]
    # The fifth card is not an AI request: it opens the Song Structures dialog (the old Templates tab: built-in and own templates, Insert Structure / Arrange with AI, music settings).
    structures_card = ft.Container(ft.Column([ft.Text("Song Structures", size=12, weight=ft.FontWeight.W_600, color=C["text1"], no_wrap=True),
                                              ft.Text("Insert or arrange a structure", size=10, color=C["text3"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)], spacing=1),
                                   bgcolor=C["panel2"], border=ft.Border.all(1, C["border"]), border_radius=8, padding=po(10, 6, 10, 6), width=168, ink=True,
                                   tooltip="Pick a built-in or your own song structure. With no sung lyrics it inserts the section tags; with lyrics the AI arranges YOUR lines into it (you preview first).",
                                   on_click=lambda e: open_structures_dialog())
    act_ring = ft.ProgressRing(width=14, height=14, stroke_width=2, visible=False)
    act_status = ft.Text("", size=11, color=C["text3"])
    actions_view = ft.Column([ft.Row([*action_cards], wrap=True, spacing=8, run_spacing=8), ft.Row([act_ring, act_status], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)],
                             spacing=6, scroll=ft.ScrollMode.AUTO)
    # Lyrics tab: a read-only summary of the ACTIVE sheet (the lyrics themselves stay in the editor above; the analysis stays in the Songwriting Assistant).
    lyr_ws_stats = ft.Text("", size=12, color=C["text2"])
    lyr_ws_outline = ft.Row(wrap=True, spacing=6, run_spacing=6)
    lyrics_ws_view = ft.Column([ft.Text("THIS SHEET", size=10, weight=ft.FontWeight.BOLD, color=C["text3"]), lyr_ws_stats,
                                ft.Text("SECTIONS", size=10, weight=ft.FontWeight.BOLD, color=C["text3"]), lyr_ws_outline], spacing=6, scroll=ft.ScrollMode.AUTO)

    def refresh_lyrics_ws():
        if not S.song:
            lyr_ws_stats.value, lyr_ws_outline.controls = "No song open.", []
        else:
            text = lyr()
            body = [l for l in text.split("\n") if l.strip() and not TP.TAG_RE.fullmatch(l.strip())]
            secs = [t for t in TP.find_tags(text) if TP.parse_tag(t.inner).kind == "section"]
            lyr_ws_stats.value = f"{len(body)} sung line(s) · {len(secs)} section(s) · {len(text)} characters"
            lyr_ws_outline.controls = [ft.Container(ft.Row([ft.Text(str(i), size=10, color=tp_block_color(t.original), weight=ft.FontWeight.W_700),
                                                          ft.Text(t.inner, size=12, color=C["text1"])], spacing=6, tight=True), padding=po(10, 4, 12, 4), border_radius=8,
                                                  bgcolor="#26" + tp_block_color(t.original)[1:], border=ft.Border.all(1, "#80" + tp_block_color(t.original)[1:]),
                                                  tooltip=f"Line {text.count(chr(10), 0, t.start) + 1}") for i, t in enumerate(secs, 1)] or [ft.Text("No section tags yet.", size=12, color=C["text3"])]
        safe_update(lyr_ws_stats, lyr_ws_outline)

    # ═══ Workspace -> Preview: listen to ONE section with temporary cues before they are written into the lyrics (section_preview.py). The section is the one the caret is in; cues, type and
    # BPM are temporary until Apply to Section; every Generate reads the lyrics of the active sheet as they are at that moment. ═══════════════════════════════════════════════════════════
    PV_TYPES = list(PV.SECTION_TYPES)
    pv = {"tg": None, "ctx": None, "key": None, "type": "Chorus", "chosen": [], "unknown": "", "bpm_on": False, "bpm": 120, "dur": PV.DEFAULT_DURATION, "all": False, "busy": "", "cancel": False,
          "msg": "", "msg_color": C["text3"], "takes": [], "takes_song": "", "sel": "", "loaded": "", "playing": False}
    class _PvAudio:
        """The preview's own channel (the global player and its queue are not touched; it is paused while a preview plays). Like the global player it tries MCI first and the Windows
        media stack when MCI cannot open the file (an mp3 from ComfyUI often is one of those: MCI error 277)."""
        def __init__(self):
            self.mci, self.wmp = MciPlayer("wppreview"), WmpPlayer()
            self.cur = self.mci

        @property
        def available(self) -> bool:
            return self.mci.available or self.wmp.available

        def load(self, path: str) -> None:
            self.mci.close(); self.wmp.close()
            first = ""
            if self.mci.available:
                try:
                    self.mci.load(path); self.cur = self.mci
                    return
                except Exception as ex:
                    first = str(ex)
            try:
                self.wmp.load(path); self.cur = self.wmp
            except Exception as ex:
                raise RuntimeError("; ".join(x for x in (first, str(ex)) if x))

        def __getattr__(self, name):
            return getattr(self.cur, name)                                  # play, pause, stop, seek, position_ms, is_playing, loaded, length_ms, set_volume, close
    pv_mci = _PvAudio()
    pv_head = ft.Text("No Section Selected", size=14, weight=ft.FontWeight.W_700, color=C["text1"], tooltip="Cues, type and BPM are only a test until you press Apply to Section.")
    pv_cue_area = ft.Row(wrap=True, spacing=6, run_spacing=6, expand=True)
    pv_tag_txt = ft.Text("", size=13, color=C["text1"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, selectable=False)
    pv_tag_box = ft.Container(pv_tag_txt, height=34, padding=po(10, 0, 10, 0), alignment=ft.Alignment(-1, 0), border_radius=8, bgcolor=C["field"], border=ft.Border.all(1, C["border"]))
    pv_type_dd = dd("", [(t, t) for t in PV_TYPES], "Chorus", lambda e: pv_set_type(e.control.value), width=122)
    pv_bpm_sw = ft.Switch(value=False, scale=0.8, on_change=lambda e: pv_set_bpm_on(bool(e.control.value)))
    pv_bpm_tf = ft.TextField(value="120", width=66, disabled=True, keyboard_type=ft.KeyboardType.NUMBER, on_change=lambda e: pv_set_bpm(e.control.value), **field_style(text_size=13, height=38))
    pv_dur_dd = dd("", [(str(d), f"{d}s") for d in PV.PREVIEW_DURATIONS], str(PV.DEFAULT_DURATION), lambda e: pv.update(dur=int(e.control.value)), width=76)
    pv_take_dd = dd("", [], None, lambda e: pv_select(e.control.value))
    pv_take_dd.expand = True
    for _d in (pv_type_dd, pv_dur_dd, pv_take_dd):                          # the caption above each control says what it is: no floating label, so each is one compact row
        _d.label, _d.height = None, 38
        _d.text_size, _d.content_padding = 11, po(8, 0, 2, 0)                  # smaller text and tighter padding so "Final Chorus" and "15s" fit whole
    pv_msg = ft.Text("", size=11, color=C["text3"], visible=False, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)       # status, at the right of the header, only when there is something to say
    pv_ring = ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False)
    pv_gen_btn = ft.FilledButton("Generate", icon=ft.Icons.PLAY_ARROW_ROUNDED, height=38, style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"), on_click=lambda e: pv_generate())
    pv_apply_btn = ft.OutlinedButton("Apply", icon=ft.Icons.CHECK_ROUNDED, height=38, tooltip="Apply to Section: write the type and cues of the tag into the lyrics (one undo step)", on_click=lambda e: pv_apply())
    pv_play_btn = ft.IconButton(ft.Icons.PLAY_ARROW_ROUNDED, icon_size=22, icon_color="#FFFFFF", bgcolor=C["accent"], width=36, height=36, on_click=lambda e: pv_play())
    pv_slider = ft.Slider(min=0, max=1000, value=0, expand=True, on_change_end=lambda e: pv_seek(float(e.control.value)))
    pv_time = ft.Text("0:00 / 0:00", size=12, color=C["text2"])
    pv_del_btn = ft.IconButton(ft.Icons.DELETE_OUTLINE_ROUNDED, icon_size=18, icon_color=C["text3"], tooltip="Delete the selected preview", on_click=lambda e: pv_delete())

    def pv_fmt(ms: float) -> str:
        s = int(max(0, ms) // 1000)
        return f"{s // 60}:{s % 60:02d}"

    def pv_say(text: str, color: str | None = None):
        pv["msg"], pv["msg_color"] = text, color or C["text3"]
        pv_msg.value, pv_msg.color, pv_msg.visible = text, pv["msg_color"], bool(text)
        safe_update(pv_msg)

    CUE_CAT = dict(CL.CATEGORIES)

    def pv_cues():
        """(shown first, the rest of the library). Recommended for the type, then every chosen cue (a chosen cue never disappears); the rest opens with All Cues."""
        rec = CL.recommended(pv["type"]) if pv["tg"] else []
        if not rec:
            rec = [c for c in CL.CUES if c.category == "vocal"][:8]
        first = list(rec) + [CL.BY_PHRASE[p] for p in pv["chosen"] if p in CL.BY_PHRASE and CL.BY_PHRASE[p] not in rec]
        return first, [c for c in CL.CUES if c not in first]

    def pv_chip(c):
        on = c.phrase in pv["chosen"]
        dis = pv["tg"] is None
        return ft.Container(ft.Row([*([ft.Icon(ft.Icons.CHECK_ROUNDED, size=13, color="#FFFFFF")] if on else []),
                                    ft.Text(c.label, size=12, color="#FFFFFF" if on else C["text1"], weight=ft.FontWeight.W_600 if on else ft.FontWeight.W_400, no_wrap=True)], spacing=4, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            height=28, padding=po(10, 0, 10, 0), border_radius=14, ink=not dis, opacity=0.5 if dis else 1.0,
                            bgcolor=C["accent"] if on else C["panel2"], border=ft.Border.all(1, C["accent"] if on else C["border"]),
                            tooltip=f"{CUE_CAT.get(c.category, c.category)}: adds “{c.phrase}” to the tag. {c.guidance}", on_click=None if dis else (lambda e, p=c.phrase: pv_toggle(p, p not in pv["chosen"])))

    def pv_tag_now() -> str:
        tg = pv["tg"]
        return PV.compose_tag(tg, pv["type"], pv["chosen"], pv["unknown"]) if tg else ""

    def pv_render_tag():
        tag = pv_tag_now()
        why = PV.check_tag(tag) if tag else ""
        pv_tag_txt.value = tag or "No section"
        pv_tag_txt.color = C["red"] if why else (C["text1"] if tag else C["text3"])
        pv_tag_box.tooltip = why or tag or None                              # a long tag is read in full here
        pv_apply_btn.disabled = pv["tg"] is None or bool(why) or tag == (pv["tg"].tag_text if pv["tg"] else "")
        pv_gen_btn.disabled = (pv["tg"] is None or bool(why)) and not pv["busy"]
        safe_update(pv_tag_txt, pv_tag_box, pv_apply_btn, pv_gen_btn)

    def pv_more_chip(rest_n: int):
        return ft.Container(ft.Row([ft.Text("− fewer" if pv["all"] else f"+ {rest_n} more", size=12, color=C["accent"], no_wrap=True)], tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            height=28, padding=po(10, 0, 10, 0), border_radius=14, ink=True, border=ft.Border.all(1, C["accent"]),
                            tooltip="Every cue of the library" if not pv["all"] else "Show only the recommended cues", on_click=lambda e: pv_toggle_all())

    def pv_render_cues():
        first, rest = pv_cues()
        chips = [pv_chip(c) for c in first] + ([pv_chip(c) for c in rest] if pv["all"] else [])
        if rest:
            chips.append(pv_more_chip(len(rest)))
        pv_cue_area.controls = chips
        safe_update(pv_cue_area)

    def pv_toggle_all():
        pv["all"] = not pv["all"]
        pv_render_cues()

    def pv_render_head():
        tg = pv["tg"]
        pv_head.value = f"{tg.name} · Line {tg.line_number}" if tg else "No Section Selected · put the cursor inside a section"
        opts = PV_TYPES + ([pv["type"]] if pv["type"] not in PV_TYPES else [])
        pv_type_dd.options = [ft.dropdown.Option(t, t) for t in opts]
        pv_type_dd.value = pv["type"]
        pv_type_dd.disabled = tg is None
        safe_update(pv_head, pv_type_dd)

    def pv_render_busy():
        busy = bool(pv["busy"])
        pv_ring.visible = busy
        pv_gen_btn.content = "Cancel" if busy else "Generate"
        pv_gen_btn.icon = ft.Icons.CLOSE_ROUNDED if busy else ft.Icons.PLAY_ARROW_ROUNDED
        safe_update(pv_ring, pv_gen_btn)

    def pv_take(tid: str = ""):
        tid = tid or pv["sel"]
        return next((t for t in pv["takes"] if t.id == tid), None)

    def pv_render_takes():
        cur = PV.relocate(lyr(), pv["tg"]) if (pv["tg"] and S.song) else None
        body_now = PV.text_hash(PV.section_body(lyr(), cur)) if cur else ""
        opts = []
        for t in pv["takes"]:
            old = bool(body_now) and t.section_body_hash != body_now and t.section_pos == cur.tag_start
            opts.append(ft.dropdown.Option(t.id, t.label + ("  ·  earlier lyrics" if old else "")))
        pv_take_dd.options = opts
        pv_take_dd.value = pv["sel"] if any(t.id == pv["sel"] for t in pv["takes"]) else None
        pv_take_dd.hint_text = "No previews yet"
        pv_take_dd.disabled = not opts
        t = pv_take()
        pv_take_dd.tooltip = (f"{t.sheet_name or 'sheet'} · {t.preview_tag} · {t.effective_bpm} BPM · {t.duration}s\n{t.created}") if t else None
        pv_del_btn.visible = bool(t)
        safe_update(pv_take_dd, pv_del_btn)

    def pv_render_player():
        t = pv_take()
        ok = bool(t) and PV.path_of(t) is not None and pv_mci.available
        pv_play_btn.disabled = not ok
        pv_play_btn.icon = ft.Icons.PAUSE_ROUNDED if pv["playing"] else ft.Icons.PLAY_ARROW_ROUNDED
        pv_play_btn.tooltip = None if ok else ("No audio device" if not pv_mci.available else "Generate a preview first")
        if not pv["playing"]:
            total = (t.measured_duration * 1000) if t else 0
            pv_time.value = f"0:00 / {pv_fmt(total)}" if t else "0:00 / 0:00"
            pv_slider.value = 0
        safe_update(pv_play_btn, pv_slider, pv_time)

    def pv_render_all():
        pv_render_head(); pv_render_cues(); pv_render_tag(); pv_render_busy(); pv_render_takes(); pv_render_player()

    def pv_load_takes():
        sid = S.song.id if S.song else ""
        if sid != pv["takes_song"]:
            pv_stop()
            pv["takes_song"] = sid
            pv["takes"] = PV.load(sid) if sid else []
            pv["sel"] = pv["takes"][-1].id if pv["takes"] else ""

    def pv_follow(force: bool = False):
        """Follow the caret: the section it is in. Rebuilds only when the section (its place or its tag) or the song / sheet changes, never on every key."""
        if not (S.ws_open and S.ws_tab == "preview"):
            return
        pv_build_layout()
        song = S.song
        tg = PV.section_at(lyr(), S.sel[0]) if song else None
        ctx = (song.id, song.book.active_id, _ui["epoch"]) if song else None
        key = (ctx, (tg.tag_text, tg.ordinal) if tg else None)
        if key == pv["key"] and not force:
            if tg:
                pv["tg"] = tg                                               # it may have moved (text above it changed): same section, same choices, nothing redrawn
            return
        pv_load_takes()
        pv["key"], pv["ctx"], pv["tg"] = key, ctx, tg
        if tg:
            pv["chosen"], pv["unknown"] = PV.read_tag(tg)
            pv["type"] = tg.key
            if S.song:
                pv["bpm"] = int(S.song.settings.bpm)
                if not pv["bpm_on"]:
                    pv_bpm_tf.value = str(pv["bpm"])
        pv_render_all()

    def pv_toggle(phrase: str, on: bool):
        if on and phrase not in pv["chosen"]:
            pv["chosen"].append(phrase)
        elif not on and phrase in pv["chosen"]:
            pv["chosen"].remove(phrase)
        pv_render_cues(); pv_render_tag()                                    # only the preview changes: no lyrics, no settings, no undo step

    def pv_set_type(t: str):
        pv["type"] = t
        pv_render_cues(); pv_render_tag()

    def pv_set_bpm_on(on: bool):
        pv["bpm_on"] = on
        pv_bpm_tf.disabled = not on
        if not on and S.song:
            pv_bpm_tf.value = str(int(S.song.settings.bpm))                  # off = the sheet's own BPM
        safe_update(pv_bpm_tf)

    def pv_set_bpm(v: str):
        try:
            n = int(float(v))
            pv_bpm_tf.error = None if 10 <= n <= 300 else "10-300"
            if 10 <= n <= 300:
                pv["bpm"] = n
        except ValueError:
            pv_bpm_tf.error = "number"
        safe_update(pv_bpm_tf)

    def pv_select(tid: str):
        pv_stop()
        pv["sel"] = tid
        pv_render_takes(); pv_render_player()

    # -- player -----------------------------------------------------------------------------------------------
    def pv_stop():
        pv["playing"] = False
        try:
            pv_mci.stop()
        except Exception:
            pass

    async def pv_tick():
        while pv["playing"]:
            try:
                pos, total = pv_mci.position_ms(), pv_mci.length_ms or 1
                pv_slider.value = min(1000.0, 1000.0 * pos / total)
                pv_time.value = f"{pv_fmt(pos)} / {pv_fmt(total)}"
                if not pv_mci.is_playing() and pos >= total - 300:
                    pv["playing"] = False
                    pv_slider.value = 0
                    pv_time.value = f"0:00 / {pv_fmt(total)}"
                    pv_render_player()
                    break
                safe_update(pv_slider, pv_time)
            except Exception:
                pv["playing"] = False
            await asyncio.sleep(0.4)

    def pv_play(e=None):
        t = pv_take()
        p = PV.path_of(t) if t else None
        if not (t and p and pv_mci.available):
            return
        if pv["playing"]:
            pv_mci.pause(); pv["playing"] = False
            pv_render_player(); return
        if player.state == "playing":
            player.toggle()                                                  # the song that was playing is paused, not stopped: its queue stays
        try:
            if pv["loaded"] != t.id or not pv_mci.loaded:
                pv_mci.load(str(p)); pv["loaded"] = t.id
            pv_mci.set_volume(player.volume)
            pv_mci.play(); pv["playing"] = True
        except Exception as ex:                                              # a file that cannot be played is a message, not a crash
            pv["loaded"], pv["playing"] = "", False
            pv_say(f"Could not play {t.label}: {ex}", C["red"])
            pv_render_player(); return
        pv_render_player()
        page.run_task(pv_tick)

    def pv_seek(v: float):
        t = pv_take()
        if t and pv_mci.loaded and pv_mci.length_ms:
            pv_mci.seek(int(pv_mci.length_ms * v / 1000.0), resume=pv["playing"])

    def pv_delete():
        t = pv_take()
        if not t:
            return

        def do():
            pv_stop(); pv["loaded"] = ""
            PV.delete(t.song_id, t.id)
            pv["takes"] = PV.load(t.song_id)
            pv["sel"] = pv["takes"][-1].id if pv["takes"] else ""
            pv_render_takes(); pv_render_player()
        confirm("Delete preview", f"Delete “{t.label}”? Its audio file is removed. Your lyrics and full-song takes are not changed.", "Delete", do, danger=True)

    # -- generate -----------------------------------------------------------------------------------------------
    def pv_generate(e=None):
        if pv["busy"]:                                                       # the same button cancels the run that is going
            pv["cancel"] = True
            pv_say("Cancelling…", C["amber"]); return
        if not S.song or pv["tg"] is None:
            toast("No Section Selected: put the cursor inside a section first.", C["amber"]); return
        if S.gen and S.gen.get("status") in ("captioning", "queued", "running", "cancelling"):
            toast("A full song is being generated. Wait for it to finish, then make the preview.", C["amber"]); return
        page.run_task(pv_generate_async)

    async def pv_generate_async():
        for _ in range(6):                                                   # the editor's last keystroke may still be on its way: wait until the editor and the model agree
            if (lyrics_tf.value or "") == lyr() or _ui["pushing"]:
                break
            await asyncio.sleep(0.1)
        song = S.song
        if not song or pv["tg"] is None or pv["busy"]:
            return
        if pv["ctx"] != (song.id, song.book.active_id, _ui["epoch"]):
            toast("The song or sheet changed. Choose the section again.", C["amber"]); return
        text = lyr()                                                         # the lyrics as they are NOW: never a copy from an earlier preview
        tg = PV.relocate(text, pv["tg"])
        if tg is None:
            toast("The section was changed or removed. Put the cursor in the section you want and try again.", C["amber"]); return
        try:
            bpm = int(pv["bpm"]) if pv["bpm_on"] else None
            req = PV.make_request(song.id, song.book.active_id, SH.active_sheet(song).name, text, tg, pv["type"], list(pv["chosen"]), pv["unknown"], copy.deepcopy(song.settings), bpm, int(pv["dur"]))
        except ValueError as ex:
            toast(str(ex), C["amber"]); return
        pv["busy"], pv["cancel"] = req["id"], False
        pv_say(f"Generating a {req['duration']} s preview of {tg.name}… you can keep editing.", C["amber"])
        pv_render_busy()
        page.run_task(pv_run, req)

    async def pv_run(req: dict):
        loop = asyncio.get_running_loop()
        res = await loop.run_in_executor(None, lambda: PV.run_preview(S.host, AUDIO_API_DIR, req, lambda: pv["cancel"]))
        pv["busy"], pv["cancel"] = "", False
        pv_render_busy()
        if res.status == "completed" and res.take:
            if S.song and S.song.id == req["song_id"]:
                pv["takes"] = PV.load(req["song_id"])
                pv["takes_song"] = req["song_id"]
                pv["sel"] = res.take.id
                pv_render_takes(); pv_render_player()
                pv_say(f"{res.take.label} is ready. Test first, then apply if you like it.", C["green"])
            else:
                pv_say("A preview finished for another song; it is saved with that song.", C["text3"])
        elif res.status == "cancelled":
            pv_say("Cancelled. Nothing was kept.", C["text3"])
        else:
            pv_say("The preview could not be made: " + (res.error or "unknown error"), C["red"])

    def pv_apply(e=None):
        tg0, song = pv["tg"], S.song
        if not song or tg0 is None:
            toast("No Section Selected.", C["amber"]); return
        if pv["ctx"] != (song.id, song.book.active_id, _ui["epoch"]):
            toast("The song or sheet changed since this section was chosen. Nothing was changed.", C["amber"]); return
        text = lyr()
        tg = PV.relocate(text, tg0)
        if tg is None or tg.tag_text != tg0.tag_text:
            toast("The section tag was changed or removed. Choose the section again; nothing was written.", C["amber"]); return
        new_tag = PV.compose_tag(tg, pv["type"], pv["chosen"], pv["unknown"])
        why = PV.check_tag(new_tag)
        if why:
            toast(why, C["amber"]); return
        ref = TP.TagRef(tg.tag_start, tg.tag_end, tg.tag_text)
        t = pv_take()
        older = bool(t) and t.section_pos == tg.tag_start and t.section_body_hash != PV.text_hash(PV.section_body(text, tg))
        if apply_section_target(section_target(ref, TP.parse_tag(ref.inner), tg.line_number), new_tag):       # only the tag changes: one undo step, the lyrics body is never written back
            if older:
                pv_say("Applied. The selected preview was made from earlier lyrics of this section.", C["amber"])

    pv_mode = {"wide": None}
    preview_view = ft.Column([], spacing=8, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    def _pv_cap(t: str):
        return ft.Text(t, size=11, color=C["text3"])

    def pv_build_layout(force: bool = False, fit: bool = True, wide: "bool | None" = None):
        """Wide Workspace: header, cues, tag + player, and ONE row of controls and buttons. Narrow: the same controls wrap into more rows (nothing is cut). Only the arrangement changes."""
        wide = (ws_width() >= 760) if wide is None else wide
        if wide == pv_mode["wide"] and not force:
            return
        pv_mode["wide"] = wide
        head = ft.Row([pv_head, ft.Container(expand=True), pv_msg, pv_ring], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        cues = ft.Row([ft.Container(_pv_cap("CUES"), padding=po(0, 6, 0, 0)), pv_cue_area], spacing=8, vertical_alignment=ft.CrossAxisAlignment.START)
        pv_bpm_tf.width = 54 if wide else 52
        pv_gen_btn.expand = pv_apply_btn.expand = not wide
        if wide:
            pv_take_dd.expand, pv_take_dd.width = False, 170
            tag_row = ft.Row([ft.Container(pv_tag_box, expand=3), pv_play_btn, ft.Container(pv_slider, expand=2), pv_time], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            ctl_row = ft.Row([pv_take_dd, pv_del_btn, ft.Container(width=1, height=20, bgcolor=C["border"]), _pv_cap("BPM"), pv_bpm_sw, pv_bpm_tf, pv_type_dd, pv_dur_dd,
                              ft.Container(expand=True), pv_gen_btn, pv_apply_btn], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
            rows = [head, cues, tag_row, ctl_row]
        else:
            pv_take_dd.expand, pv_take_dd.width = True, None
            rows = [head, cues, pv_tag_box, ft.Row([pv_play_btn, pv_slider, pv_time], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Row([pv_take_dd, pv_del_btn], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Row([_pv_cap("BPM"), pv_bpm_sw, pv_bpm_tf, pv_type_dd, pv_dur_dd], spacing=8, wrap=True, run_spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ft.Row([pv_gen_btn, pv_apply_btn], spacing=8)]
        preview_view.controls = rows
        safe_update(preview_view)
        if fit:
            fit_workspace_height()

    pv_build_layout(force=True, fit=False, wide=False)                                  # the first arrangement, while the window is still being built (the height is fitted later)

    btn_ws = ibtn(ft.Icons.EXPAND_MORE_ROUNDED, "Collapse Workspace", lambda e: toggle_ws())
    workspace_panel = panel(ft.Column([ft.Row([ft.Text("Workspace", size=14, weight=ft.FontWeight.BOLD, color=C["text1"]), ws_tabs_row, ft.Container(expand=True), btn_ws],
                                              spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER), ws_content], spacing=6, expand=True), padding=10)
    workspace_holder = ft.Container(content=workspace_panel, height=S.ws_h)

    # ═══ Templates & Presets (Workspace -> Templates): song structures, custom lyrics tools, saved AI prompts. Data in presets.py / music_studio_presets.json; every edit of the lyrics goes
    # through set_lyrics (one undo step), every AI request through run_action (the AI Actions engine). Nothing from a preset is executed. ═════════════════════════════════════════════
    tp = {"sub": "struct", "sel": "builtin:pop", "exp": False, "busy": False}       # exp = the Templates workspace is expanded (the saved Workspace height is not touched)
    TP_SUBS = [("struct", "Song Structures")] + ([("tools", "Custom Tools"), ("prompts", "AI Prompts")] if TEMPLATE_EXTRAS_VISIBLE else [])
    tp_tabs_row = ft.Row(spacing=4)
    tp_help_btn = ft.IconButton(ft.Icons.HELP_OUTLINE_ROUNDED, icon_size=18, width=34, height=34, padding=0, icon_color=C["text3"], tooltip="")
    tp_exp_btn = ft.IconButton(ft.Icons.OPEN_IN_FULL_ROUNDED, icon_size=18, width=34, height=34, padding=0, icon_color=C["text2"], on_click=lambda e: toggle_tp_expand())
    tp_go_btn = ft.FilledButton("Insert Structure", icon=ft.Icons.PLAYLIST_ADD_ROUNDED, height=44, on_click=lambda e: start_template_use(), style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))
    tp_go_ring = ft.ProgressRing(width=18, height=18, stroke_width=2, visible=False)
    tp_warn = ft.Text("", size=11, color=C["amber"], visible=False, selectable=True)
    tp_body = ft.Column(spacing=8)
    templates_view = ft.Column([ft.Row([tp_tabs_row, ft.Container(expand=True), tp_help_btn, tp_exp_btn], spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER), tp_warn, tp_body], spacing=6, scroll=ft.ScrollMode.AUTO)

    def manage_presets(sub: str):
        tp["sub"] = sub if sub in dict(TP_SUBS) else "struct"
        open_structures_dialog()

    _tp_dlg = {"open": False, "w": 0}                                      # the Song Structures dialog (the old Templates tab) and the width its content has

    def open_structures_dialog():
        if _tp_dlg["open"]:
            return
        w = int(max(560, min(1000, page_w() - 40)))
        h = int(max(380, min(700, page_h() - 60)))
        _tp_dlg.update(open=True, w=w, h=h)
        compact = w < 760
        lw = 210 if compact else (270 if w < 900 else 300)
        render_structures()

        def close(e=None):
            if _tp_dlg["open"]:
                _tp_dlg["open"] = False
                page.pop_dialog()
        header = ft.Container(ft.Row([ft.Column([ft.Text("Song Structures", size=26 if not compact else 22, weight=ft.FontWeight.W_700, color=C["text1"]),
                                                 ft.Text("Choose a template or create your own", size=13, color=C["text2"])], spacing=2, expand=True),
                                      ibtn(ft.Icons.CLOSE_ROUNDED, "Close", close, 22, w=40)], vertical_alignment=ft.CrossAxisAlignment.START), padding=po(24, 16, 14, 12))
        body = ft.Row([ft.Container(sd_left, width=lw, padding=po(14, 10, 10, 10), border=ft.Border(right=ft.BorderSide(1, C["border"]))),
                       ft.Container(sd_right, expand=True, padding=po(18, 12, 20, 10))], spacing=0, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH)
        footer = ft.Container(ft.Row([sd_dup_btn, ft.Container(expand=True), ft.OutlinedButton("Close", height=44, on_click=close), tp_go_ring, tp_go_btn], spacing=10,
                                     vertical_alignment=ft.CrossAxisAlignment.CENTER), padding=po(20, 12, 20, 14), border=ft.Border(top=ft.BorderSide(1, C["border"])))      # never scrolls away
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], content_padding=0, shape=ft.RoundedRectangleBorder(radius=16),
                                        content=ft.Container(ft.Column([header, body, footer], spacing=0), width=w, height=h),
                                        on_dismiss=lambda e: _tp_dlg.update(open=False)))
        _tp_dlg["close"] = close

    def close_structures_dialog():
        c = _tp_dlg.get("close")
        if _tp_dlg["open"] and c:
            c()

    def tp_icon(icon, tip, fn):
        return ft.IconButton(icon, icon_size=18, width=34, height=34, padding=0, tooltip=tip, icon_color=C["text2"], on_click=lambda e: fn())

    def tp_label(text):
        return ft.Text(text, size=11, weight=ft.FontWeight.BOLD, color=C["text3"])

    def open_form(title, controls, on_save, ok_label="Save", extra=None, width=440, after=None):
        """A dialog of its own (never inside the Workspace). on_save() raises PS.PresetError with a sentence; it is shown in the dialog and nothing is closed."""
        err = ft.Text("", size=11, color=C["red"], selectable=True)

        def do(e):
            try:
                on_save()
            except (PS.PresetError, TL.TimelineError, OSError) as ex:
                err.value = str(ex)
                safe_update(err)
                return
            page.pop_dialog()
            if after is not None:
                after()
            else:
                render_templates(); rebuild_custom_tools()
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Text(title), content=ft.Container(width=width, content=ft.Column([*controls, err], spacing=8, tight=True, scroll=ft.ScrollMode.AUTO)),
                                        actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), *(extra or []),
                                                 ft.FilledButton(ok_label, on_click=do, style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))]))

    def ask_delete(what: str, name: str, fn):
        def do():
            try:
                fn()
            except (PS.PresetError, OSError) as ex:
                toast(str(ex), C["red"]); return
            render_templates(); rebuild_custom_tools()
        confirm(f"Delete {what}", f"Delete “{name}”? This cannot be undone. Your songs are not changed.", "Delete", do, danger=True)

    # -- song structures ----------------------------------------------------------------------------------------
    def tp_select(tid: str):
        tp["sel"] = tid
        render_templates()

    def open_template_dialog(tid: str | None = None):
        """New / Edit Song Template: a layout of its own (the shared open_form() still serves the other dialogs). Header, name + description, a heading above a fixed-height structure
        field that scrolls inside itself, a short help line, and a footer that never moves; only the body scrolls, and only when the window is too low."""
        cur = next((t for t in pstore.templates if t.id == tid), None) if tid else None
        name_tf = ft.TextField(hint_text="e.g. Modern K-Pop", value=cur.name if cur else "", **field_style(height=44, hint_style=ft.TextStyle(color=C["text3"], size=12)))
        desc_tf = ft.TextField(hint_text="Describe the arrangement", value=cur.description if cur else "", **field_style(height=44, hint_style=ft.TextStyle(color=C["text3"], size=12)))
        struct_tf = ft.TextField(multiline=True, min_lines=12, max_lines=12, value=cur.structure if cur else PS_DEFAULT_STRUCTURE,       # exactly 12 lines tall; longer text scrolls inside the field
                                 hint_text="[Intro]\n\n[Verse 1]\n\n[Chorus]", **field_style(height=None, text_size=13, hint_style=ft.TextStyle(color=C["text3"], size=12)))
        err = ft.Text("", size=12, color=C["red"], selectable=True)

        def import_tags(e):
            s = PS.structure_of(lyr()) if S.song else ""
            if not s:
                toast("The active sheet has no section tags yet.", C["amber"]); return
            struct_tf.value = s                                     # only the tags of the active sheet, never its lyric lines; nothing is saved until Save Template
            err.value = ""
            safe_update(struct_tf, err)

        def save(e):
            try:
                new = pstore.save_template(tid, name_tf.value, desc_tf.value, struct_tf.value)
            except (PS.PresetError, OSError) as ex:
                err.value = str(ex); safe_update(err); return        # the dialog stays open, nothing was changed
            tp["sel"] = new.id
            page.pop_dialog()
            render_templates(); rebuild_custom_tools()

        def labelled(text, ctl):
            return ft.Column([ft.Text(text, size=12, weight=ft.FontWeight.W_600, color=C["text2"]), ctl], spacing=4, expand=True)
        width = int(max(400, min(680, page_w() - 80)))
        body_h = int(max(240, min(395, page_h() - 270)))             # the body is as tall as its content (~400 px); a low window scrolls it while the header and the footer stay (270 = header + footer + dialog margins)
        header = ft.Row([ft.Column([ft.Text("Edit Song Template" if cur else "New Song Template", size=20, weight=ft.FontWeight.W_700, color=C["text1"]),
                                    ft.Text("Change this template's structure" if cur else "Create a reusable song structure", size=12, color=C["text2"])], spacing=2, expand=True),
                         ft.IconButton(ft.Icons.CLOSE_ROUNDED, icon_size=20, tooltip="Close", icon_color=C["text2"], on_click=lambda e: page.pop_dialog())], vertical_alignment=ft.CrossAxisAlignment.START)
        body = ft.Column([ft.Row([labelled("Template Name", name_tf), labelled("Description", desc_tf)], spacing=12, vertical_alignment=ft.CrossAxisAlignment.START),
                          ft.Row([ft.Column([ft.Text("Song Structure", size=14, weight=ft.FontWeight.W_700, color=C["text1"]), ft.Text("One section tag per block", size=12, color=C["text3"])], spacing=0, expand=True),
                                  ft.TextButton("Import from Active Sheet", icon=ft.Icons.DOWNLOAD_ROUNDED, on_click=import_tags, tooltip="Copies the section tags of the active sheet here; its lyrics are not copied",
                                                style=ft.ButtonStyle(color=C["accent"]))], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                          struct_tf,
                          ft.Row([ft.Icon(ft.Icons.INFO_OUTLINE_ROUNDED, size=15, color=C["text3"]),
                                  ft.Text("Tags are kept as written, e.g. [Chorus - powerful]. Import copies section tags only, never lyrics.", size=11, color=C["text3"], expand=True)],
                                 spacing=6, vertical_alignment=ft.CrossAxisAlignment.START)], spacing=10, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], title=None, content_padding=po(24, 20, 24, 8), actions_padding=po(24, 8, 24, 16),
                                        content=ft.Container(width=width, content=ft.Column([header, ft.Container(body, height=body_h), err], spacing=10, tight=True)),
                                        actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                                                 ft.FilledButton("Save Template", icon=ft.Icons.CHECK_ROUNDED, on_click=save, style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))]))

    def duplicate_template(tid: str):
        try:
            tp["sel"] = pstore.duplicate_template(tid).id
        except (PS.PresetError, OSError) as ex:
            toast(str(ex), C["red"]); return
        render_templates()

    tp_ai_status = ft.Text("", size=12, color=C["amber"], visible=False)
    _tp_w = {"w": 0.0}                                                      # the Workspace width the Templates tab was last drawn for

    def start_template_use():
        """Use the selected song structure on the ACTIVE sheet. No sung lyrics: the template becomes the sheet's structure (no AI, one undo step). Sung lyrics: the AI arranges the existing
        lines into the template, Python checks the answer, and the user previews it before choosing Replace Current Sheet / Open in New Sheet / Cancel. Everything the request needs is
        captured here, before the AI is asked."""
        if not S.song:
            toast("Open or create a song first.", C["amber"]); return
        tpl = next((t for t in pstore.all_templates() if t.id == tp["sel"]), None)
        if tpl is None:
            return
        try:
            TA.template_tags(tpl.structure)
        except TA.ArrangeError as ex:
            toast(str(ex), C["amber"]); return
        text = lyr()
        if not TA.has_sung_lyrics(text):                                    # Case A: sections only (or nothing): the template is the structure, no AI
            new_text = TA.plain_template_text(tpl.structure)
            if new_text == text:
                toast("The sheet already has this structure.", C["amber"]); return
            close_structures_dialog()
            set_lyrics(new_text, (0, 0))
            toast(f"“{tpl.name}” structure applied to this sheet (Undo restores the previous text)", C["green"])
            return
        if tp["busy"] or S.chat_busy_song or S.an_busy:
            toast("The AI is still busy. Try again in a moment.", C["amber"]); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        ctx = {"song_id": S.song.id, "sheet_id": S.song.book.active_id, "sheet_name": SH.active_sheet(S.song).name, "text": text, "hash": AC.text_hash(text),
               "settings": SH.settings_to_dict(S.song.settings), "tpl": tpl, "title": S.song.title,
               "hint": settings_hint(S.song.settings), "backend": S.backend, "host": S.host}
        close_structures_dialog()                                           # the AI works in the background; the preview opens when it is ready
        page.run_task(run_arrangement, ctx)

    async def run_arrangement(ctx: dict):
        loop = asyncio.get_running_loop()
        ask = spec_ask("music_producer", None, "arrange")                    # sections and cues are the Music Producer's work
        S.chat_busy_song = ctx["song_id"]
        tp["busy"] = True
        tp_ai_status.value, tp_ai_status.color, tp_ai_status.visible = f"The AI is arranging your lyrics into “{ctx['tpl'].name}”…", C["amber"], True
        refresh_tp_primary(); safe_update(tp_ai_status); refresh_actions_ui()
        arranged, failure = None, None
        try:
            arranged = await loop.run_in_executor(None, lambda: TA.arrange(ask, ctx["title"], ctx["hint"], ctx["text"], ctx["tpl"].name, ctx["tpl"].structure))
        except TA.ArrangeError as ex:
            failure = ex
            log.debug("template arrangement refused: %s\nRAW:\n%s", ex.problems, ex.raw)
        except Exception as ex:
            failure = TA.ArrangeError(f"AI error: {ex}")
        finally:
            S.chat_busy_song = ""
            tp["busy"] = False
            tp_ai_status.visible = False
            refresh_tp_primary(); safe_update(tp_ai_status); refresh_actions_ui()
            TRC.log_event("arrange", song_id=ctx["song_id"], template=ctx["tpl"].name, lyrics=ctx["text"], failed=failure is not None, error=str(failure) if failure is not None else "",
                          problems=list(getattr(failure, "problems", []) or [])[:10], raw_answer=(getattr(failure, "raw", "") or "") if failure is not None else "",
                          warnings=(list(arranged.warnings)[:10] if arranged is not None else []))
        if failure is not None:
            tp_ai_status.value, tp_ai_status.color, tp_ai_status.visible = "The last arrangement could not be made. Nothing was changed.", C["red"], True
            safe_update(tp_ai_status)
            show_arrangement_failure(ctx, failure)
        else:
            show_arrangement_preview(ctx, arranged, arranged.music)

    def show_arrangement_failure(ctx: dict, ex: "TA.ArrangeError"):
        raw = (ex.raw or "").strip()
        items = [ft.Text("Nothing was changed: no lyrics were edited, no sheet was created and no undo step was added. The AI is not asked again automatically.", size=12, color=C["text2"])]
        items += [ft.Text("• " + p, size=12, color=C["red"], selectable=True) for p in ex.problems[:10]]
        if raw:
            items.append(ft.ExpansionTile(title=ft.Text("Raw AI answer (debug)", size=12, color=C["text2"]), controls=[
                ft.Container(ft.Text(raw[:6000], size=11, color=C["text1"], font_family="Consolas", selectable=True), bgcolor=C["field"], border_radius=8, padding=10)]))
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Text("The AI arrangement was not usable"), content=ft.Container(width=520, height=dlg_h(360), content=ft.Column(
            items, spacing=8, scroll=ft.ScrollMode.AUTO)), actions=[ft.TextButton("Close", on_click=lambda e: page.pop_dialog())]))

    def show_arrangement_preview(ctx: dict, arr: "TA.Arranged | None", music: "TA.Music | None" = None):
        """Preview of the AI's answer. arr = the arranged lyrics (None for the separate `Suggest Music Settings`), music = the suggested music settings. Everything the AI suggested for the
        music is editable here. Nothing changes until Apply; Apply Lyrics Only never touches the options; the destination is Replace Current Sheet or Open in New Sheet."""
        tpl = ctx["tpl"]
        music = music if music is not None else (arr.music if arr is not None else None)
        cur = ctx["settings"]                                                      # the options of the sheet when the request was made
        ai = dict(music.values) if music else {}
        init = {f: ai.get(f, cur.get(f, "")) for f in TA.MUSIC_FIELDS}

        def box(label, text):
            return ft.Column([ft.Text(label, size=11, weight=ft.FontWeight.BOLD, color=C["text3"]),
                              ft.Container(ft.Column([ft.Text(text, size=12, color=C["text1"], font_family="Consolas", selectable=True)], scroll=ft.ScrollMode.AUTO), bgcolor=C["field"],
                                           border_radius=8, padding=10, border=ft.Border.all(1, C["border"]), height=190, width=365)], spacing=4, width=365)

        style_tf = ft.TextField(label="Music Style Prompt", value=str(init["style_prompt"] or ""), multiline=True, min_lines=2, max_lines=4, width=740, **field_style(height=None, text_size=12))
        bpm_tf = ft.TextField(label="BPM", value=str(init["bpm"]), width=90, **field_style(height=44))
        dur_tf = ft.TextField(label="Duration (s)", value=str(init["duration"]), width=110, **field_style(height=44))
        key_opts = list(MO_KEYS) if str(init["keyscale"]) in MO_KEYS else [str(init["keyscale"])] + list(MO_KEYS)
        key_dd = dd("Key", [(k, k) for k in key_opts], str(init["keyscale"]), lambda e: None, width=140)
        ts_dd = dd("Time Signature", list(TIMESIGS), str(init["timesignature"]), lambda e: None, width=140)
        mood_opts = [(m, m or "None") for m in MOODS] + ([] if init["mood"] in MOODS else [(init["mood"], init["mood"])])
        mood_dd = dd("Mood", mood_opts, init["mood"], lambda e: None, width=150)
        vocal_dd = dd("Vocal Style", VOCALS, init["vocal"], lambda e: None, width=140)
        energy_dd = dd("Energy", ENERGIES, init["energy"], lambda e: None, width=130)
        ctl = {"style_prompt": style_tf, "bpm": bpm_tf, "keyscale": key_dd, "timesignature": ts_dd, "duration": dur_tf, "mood": mood_dd, "vocal": vocal_dd, "energy": energy_dd}
        dest = ft.RadioGroup(content=ft.Row([ft.Radio(value="replace", label="Replace Current Sheet" if arr is not None else "Apply to Current Sheet"),
                                             ft.Radio(value="new", label="Open in New Sheet")], spacing=16), value="replace")
        err = ft.Text("", size=11, color=C["red"], selectable=True)
        problems = [f"{TA.FIELD_LABELS.get(f, f)}: the AI wrote “{raw[:60]}” ({why}); it is not applied." for f, raw, why in (music.problems if music else [])]

        def edited_values() -> tuple:
            """(values to apply, errors): a field is applied when the AI gave a valid value for it or the user edited it; every one is checked again."""
            vals, errs = {}, []
            for f in TA.MUSIC_FIELDS:
                raw = ctl[f].value
                raw = "" if raw is None else str(raw)
                if f in ai and raw == str(init[f]):
                    vals[f] = ai[f]
                elif raw != str(init[f]):
                    v, why = TA.check_field(f, raw)
                    if why:
                        errs.append(f"{TA.FIELD_LABELS[f]}: {why}")
                    else:
                        vals[f] = v
            return vals, errs

        def apply(with_music: bool):
            vals, errs = edited_values() if with_music else ({}, [])
            if errs:
                err.value = "Fix before applying: " + " · ".join(errs); safe_update(err); return
            where = dest.value
            page.pop_dialog()
            if where == "replace":
                if not (S.song and S.song.id == ctx["song_id"]):
                    toast("The song changed. Nothing was applied: open that song and use the template again.", C["amber"]); return
                if S.song.book.active_id != ctx["sheet_id"]:
                    toast(f"The open sheet is not “{ctx['sheet_name']}” any more. Nothing was applied: open that sheet, or choose Open in New Sheet.", C["amber"]); return
                if arr is not None and AC.text_hash(lyr()) != ctx["hash"]:
                    toast("The lyrics of this sheet changed while the AI was working. Nothing was applied: use the template again, or choose Open in New Sheet.", C["amber"]); return
                if arr is not None:
                    set_lyrics(arr.text, (0, 0))                                  # one undo step
                if vals:
                    try:
                        SH.apply_settings(S.song, ctx["sheet_id"], TA.music_overrides(vals))      # the request's own sheet, by id: no other sheet can be touched
                    except SH.SheetError as ex:
                        toast(str(ex), C["amber"]); return
                    build_settings(); mark_dirty()                                # the Style / Output controls show what the sheet now holds
                toast(("Lyrics and music settings applied" if (arr is not None and vals) else "Lyrics applied" if arr is not None else "Music settings applied")
                      + (" (Undo restores the previous lyrics)" if arr is not None else ""), C["green"])
            else:
                if not (S.song and S.song.id == ctx["song_id"]):
                    toast("The song changed. No sheet was created: open that song and use the template again.", C["amber"]); return
                settings = dict(ctx["settings"])                                  # the request snapshot, not whatever the sheet holds now
                settings.update(TA.music_overrides(vals))
                name = f"{tpl.name} AI" if arr is not None else "Music Ideas"
                sh = sheet_change(lambda song: SH.from_snapshot(song, name, arr.text if arr is not None else ctx["text"], settings, None))
                if sh is not None:
                    toast(f"Opened “{sh.name}”. The original sheet is unchanged.", C["green"])

        blocks = []
        if arr is not None:
            empty = sum(1 for _, ls in arr.sections if not ls)
            blocks = [ft.Row([box("ORIGINAL", ctx["text"]), box("ARRANGED LYRICS", arr.text)], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START),
                      ft.Text(f"{len(TA.line_groups(ctx['text'], {t.strip() for t in TA.template_tags(tpl.structure)}))} lines placed in {len(arr.sections)} sections · {arr.moved} moved to another kind of section · "
                              f"{empty} sections left empty (no lyrics were written)", size=11, color=C["text3"]),
                      *([ft.Text("Decided by the program: " + " · ".join(arr.notes), size=11, color=C["text2"], selectable=True)] if arr.notes else []),
                      *[ft.Text("⚠ " + w, size=11, color=C["amber"], selectable=True) for w in arr.warnings]]
        if music is not None:
            blocks += [ft.Divider(height=1, color=C["border"]), ft.Text("MUSIC SETTINGS — suggested by the AI, editable (applying them sets Style mode to Advanced)", size=11, weight=ft.FontWeight.BOLD, color=C["text3"]),
                       style_tf, ft.Row([bpm_tf, key_dd, ts_dd, dur_tf], spacing=8, wrap=True, run_spacing=8), ft.Row([mood_dd, vocal_dd, energy_dd], spacing=8, wrap=True, run_spacing=8),
                       *[ft.Text("⚠ " + p, size=11, color=C["amber"], selectable=True) for p in problems]]
        else:
            blocks.append(ft.Text("The AI returned no music settings this time. You can still apply the lyrics.", size=11, color=C["amber"]))
        blocks += [ft.Text("DESTINATION", size=11, weight=ft.FontWeight.BOLD, color=C["text3"]), dest, err]
        actions = [ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog())]
        if arr is not None:
            actions.append(ft.OutlinedButton("Apply Lyrics Only", on_click=lambda e: apply(False)))
        if music is not None:
            actions.append(ft.FilledButton("Apply Lyrics + Music Settings" if arr is not None else "Apply Music Settings", on_click=lambda e: apply(True), style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF")))
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Text(f"AI Template Arrangement · {tpl.name}" if arr is not None else "Suggested Music Settings"),
                                        content=ft.Container(width=760, height=dlg_h(520), content=ft.Column([ft.Text(f"From the sheet “{ctx['sheet_name']}”. Nothing changes until you choose.", size=12, color=C["text2"]), *blocks],
                                                                                                      spacing=8, scroll=ft.ScrollMode.AUTO)), actions=actions))

    def settings_hint(s) -> dict:
        """What the AI is told about the sheet's current choices (read only)."""
        return {"genre": s.genre, "mood": s.mood, "vocal": s.vocal, "energy": s.energy, "language": s.language, "bpm": s.bpm, "keyscale": s.keyscale, "timesignature": s.timesignature,
                "duration": s.duration, "style_mode": s.style_mode, "style_prompt": s.style_prompt}

    def start_music_suggestion():
        """`Suggest Music Settings`: a separate command, never run automatically. Works with or without lyrics (an empty sheet sends the template's planned sections)."""
        if not S.song:
            toast("Open or create a song first.", C["amber"]); return
        tpl = next((t for t in pstore.all_templates() if t.id == tp["sel"]), None)
        if tp["busy"] or S.chat_busy_song or S.an_busy:
            toast("The AI is still busy. Try again in a moment.", C["amber"]); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        text = lyr()
        s = S.song.settings
        ctx = {"song_id": S.song.id, "sheet_id": S.song.book.active_id, "sheet_name": SH.active_sheet(S.song).name, "text": text, "hash": AC.text_hash(text),
               "settings": SH.settings_to_dict(s), "tpl": tpl, "title": S.song.title, "hint": settings_hint(s),
               "backend": S.backend, "host": S.host, "music_only": True}
        page.run_task(run_music_suggestion, ctx)

    async def run_music_suggestion(ctx: dict):
        loop = asyncio.get_running_loop()
        ask = spec_ask("music_director", None, "music_suggestion")           # the music style and caption are the Music Director's work (its own session)
        planned = []
        if ctx["tpl"] is not None and not TA.has_sung_lyrics(ctx["text"]):
            try:
                planned = TA.template_tags(ctx["tpl"].structure)
            except TA.ArrangeError:
                planned = []
        S.chat_busy_song = ctx["song_id"]
        tp["busy"] = True
        tp_ai_status.value, tp_ai_status.color, tp_ai_status.visible = "The AI is suggesting music settings…", C["amber"], True
        refresh_tp_primary(); safe_update(tp_ai_status); refresh_actions_ui()
        music, failure = None, None
        try:
            music = await loop.run_in_executor(None, lambda: TA.suggest_music(ask, ctx["title"], ctx["hint"], ctx["text"], planned))
        except TA.ArrangeError as ex:
            failure = ex
            log.debug("music suggestion refused: %s\nRAW:\n%s", ex.problems, ex.raw)
        except Exception as ex:
            failure = TA.ArrangeError(f"AI error: {ex}")
        finally:
            S.chat_busy_song = ""
            tp["busy"] = False
            tp_ai_status.visible = False
            refresh_tp_primary(); safe_update(tp_ai_status); refresh_actions_ui()
        if failure is not None:
            show_arrangement_failure(ctx, failure)
        else:
            show_arrangement_preview(ctx, None, music)

    def ws_width() -> float:
        """The width the Workspace really has (window minus the AI column, the sidebar and paddings), so the Templates layout can use all of it and never leaves a blank right side."""
        try:
            side = sidebar_holder.width if sidebar_holder.width else 0
            return max(300.0, page_w() - 20 - (ai_holder.width or S.ai_w) - 8 - side - 8 - 40)
        except Exception:
            return 640.0

    def tp_block_color(tag: str) -> str:
        t = tag.strip("[] ").lower()
        if "pre" in t and "chorus" in t:
            return "#60A5FA"
        if "chorus" in t or "hook" in t:
            return C["amber"]
        if "verse" in t:
            return C["accent"]
        if "bridge" in t:
            return C["green"]
        if any(w in t for w in ("instrumental", "solo", "break", "interlude", "build", "drop")):
            return "#A78BFA"
        if any(w in t for w in ("intro", "outro")):
            return "#2BB5C0"
        return C["text3"]

    def tp_blocks(tags: list, big: bool) -> "ft.Row":
        """The arrangement as labelled blocks in template order. Repeated sections stay distinct (each block carries its position); colour follows the kind of section."""
        out = []
        for i, t in enumerate(tags, 1):
            col = tp_block_color(t)
            out.append(ft.Container(ft.Row([ft.Text(str(i), size=10, color=col, weight=ft.FontWeight.W_700),
                                            ft.Text(t.strip().strip("[]"), size=13 if big else 12, color=C["text1"])], spacing=6, tight=True),
                                    padding=po(10, 6 if big else 4, 12, 6 if big else 4), border_radius=8, bgcolor="#26" + col[1:], border=ft.Border.all(1, "#80" + col[1:]), tooltip=t))
        return ft.Row(out, wrap=True, spacing=6, run_spacing=6)

    def refresh_tp_primary():
        """The one primary action follows the sheet: no sung lyrics -> Insert Structure (no AI); sung lyrics -> Arrange with AI. Busy: disabled with a progress ring."""
        has = bool(S.song) and TA.has_sung_lyrics(lyr())
        tp_go_btn.content = "Arrange with AI" if has else "Insert Structure"
        tp_go_btn.icon = ft.Icons.AUTO_AWESOME_ROUNDED if has else ft.Icons.PLAYLIST_ADD_ROUNDED
        tp_go_btn.disabled = tp["busy"] or not S.song
        tp_go_btn.tooltip = ("Open or create a song first" if not S.song else ("The AI is working…" if tp["busy"] else
                             ("Your own lines are placed into this structure by the AI (it never writes or changes lyrics); you preview before anything changes" if has
                              else "Replace this sheet's empty section tags with the structure (one undo step, no AI)")))
        tp_go_ring.visible = tp["busy"]
        safe_update(tp_go_btn, tp_go_ring)
    _hooks["tp_primary"] = refresh_tp_primary

    SD_ICONS = {"builtin:pop": (ft.Icons.MUSIC_NOTE_ROUNDED, "#F472B6"), "builtin:rock": (ft.Icons.BOLT_ROUNDED, "#F87171"), "builtin:alt_rock": (ft.Icons.AUDIOTRACK_ROUNDED, "#60A5FA"),
                "builtin:ballad": (ft.Icons.FAVORITE_ROUNDED, "#A78BFA"), "builtin:acoustic": (ft.Icons.LIBRARY_MUSIC_ROUNDED, "#34D399"), "builtin:indie_pop": (ft.Icons.STAR_ROUNDED, "#F5B83D"),
                "builtin:edm": (ft.Icons.GRAPHIC_EQ_ROUNDED, "#E879F9"), "builtin:rnb": (ft.Icons.MIC_ROUNDED, "#8B7CF6"), "builtin:hiphop": (ft.Icons.HEADPHONES_ROUNDED, "#FB923C")}
    sd_left = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, expand=True)         # the template library (scrolls by itself, the dialog does not)
    sd_right = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)       # the selected template
    sd_dup_btn = ft.OutlinedButton("Duplicate to Edit", icon=ft.Icons.CONTENT_COPY_ROUNDED, height=44)

    def _sd_count(t) -> int:
        try:
            return len(TA.template_tags(t.structure))
        except TA.ArrangeError:
            return len([l for l in t.structure.split("\n") if l.strip()])

    def _sd_item(t, builtin: bool, word: bool):
        sel = tp["sel"] == t.id
        ic, col = SD_ICONS.get(t.id, (ft.Icons.QUEUE_MUSIC_ROUNDED, C["text2"]))
        n = _sd_count(t)
        fg2 = "#CCFFFFFF" if sel else C["text3"]
        row = [ft.Icon(ic, size=20, color="#FFFFFF" if sel else col),
               ft.Text(t.name, size=14, color="#FFFFFF" if sel else C["text1"], weight=ft.FontWeight.W_700 if sel else ft.FontWeight.W_500, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True, tooltip=t.name),
               ft.Text(f"{n} sections" if word else str(n), size=12, color=fg2)]
        if not builtin:
            row.append(ft.PopupMenuButton(icon=ft.Icons.MORE_VERT_ROUNDED, icon_size=16, icon_color=fg2, tooltip="Edit, duplicate or delete", items=[
                ft.PopupMenuItem(content=ft.Text("Edit"), on_click=lambda e, i=t.id: open_template_dialog(i)),
                ft.PopupMenuItem(content=ft.Text("Duplicate"), on_click=lambda e, i=t.id: duplicate_template(i)),
                ft.PopupMenuItem(content=ft.Text("Delete…"), on_click=lambda e, i=t.id, n=t.name: ask_delete("template", n, lambda: pstore.delete_template(i)))]))
        return ft.Container(ft.Row(row, spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER), padding=po(12, 3 if not builtin else 7, 4 if not builtin else 12, 3 if not builtin else 7),
                            border_radius=10, ink=True, bgcolor=C["accent"] if sel else C["panel2"], border=ft.Border.all(1, C["accent"] if sel else C["border"]), on_click=lambda e, i=t.id: tp_select(i))

    def _sd_card(label: str, body: list, right=None):
        head = ft.Row([ft.Text(label, size=11, weight=ft.FontWeight.BOLD, color=C["text3"]), ft.Container(expand=True)] + ([right] if right else []), vertical_alignment=ft.CrossAxisAlignment.CENTER)
        return ft.Container(ft.Column([head, *body], spacing=10, horizontal_alignment=ft.CrossAxisAlignment.STRETCH), padding=14, border_radius=12, bgcolor=C["panel2"], border=ft.Border.all(1, C["border"]))

    def render_structures():
        all_t = pstore.all_templates()
        if tp["sel"] not in {t.id for t in all_t}:
            tp["sel"] = "builtin:pop"
        cur = next(t for t in all_t if t.id == tp["sel"])
        builtin = cur.id.startswith("builtin:")
        try:
            tags = TA.template_tags(cur.structure)
        except TA.ArrangeError:
            tags = []
        compact = _tp_dlg["w"] < 760
        word = not compact and _tp_dlg["w"] >= 860                              # "10 sections" where there is room, "10" where there is not
        sd_left.controls = [tp_label("BUILT-IN TEMPLATES"), *[_sd_item(t, True, word) for t in all_t if t.id.startswith("builtin:")],
                            ft.Container(height=2), tp_label("MY TEMPLATES"), *[_sd_item(t, False, word) for t in pstore.templates],
                            ft.Container(ft.Row([ft.Icon(ft.Icons.ADD_ROUNDED, size=20, color=C["accent"]), ft.Text("New Template", size=14, color=C["accent"])], spacing=10), padding=po(12, 8, 12, 8),
                                         border_radius=10, ink=True, bgcolor=C["panel2"], border=ft.Border.all(1, C["border"]), on_click=lambda e: open_template_dialog(None), tooltip="Create your own song structure")]
        ic, col = SD_ICONS.get(cur.id, (ft.Icons.QUEUE_MUSIC_ROUNDED, C["text2"]))
        head = ft.Row([ft.Container(ft.Icon(ic, size=30, color=col), width=56, height=56, alignment=ft.Alignment(0, 0), border_radius=12, bgcolor="#26" + col[1:] if col.startswith("#") and len(col) == 7 else C["panel2"],
                                    border=ft.Border.all(1, "#80" + col[1:] if col.startswith("#") and len(col) == 7 else C["border"])),
                       ft.Column([ft.Text(cur.name, size=24 if not compact else 20, weight=ft.FontWeight.W_700, color=C["text1"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                  ft.Text(f"{len(tags)} sections" + ("" if builtin else " · my template"), size=13, color=C["text2"]),
                                  *([ft.Text(cur.description, size=12, color=C["text2"])] if cur.description else [])], spacing=2, expand=True),
                       ft.IconButton(ft.Icons.QUEUE_MUSIC_ROUNDED, icon_size=20, tooltip="Suggest Music Settings (the AI proposes a style prompt, BPM, key...; you preview before applying)", icon_color=C["text2"],
                                     on_click=lambda e: start_music_suggestion())], spacing=14, vertical_alignment=ft.CrossAxisAlignment.START)
        text_h = int(max(90, min(190, _tp_dlg["h"] - 420)))
        text_card = _sd_card("COMPLETE TEMPLATE TEXT", [ft.Container(ft.Column([ft.Text(cur.structure, size=12, color=C["text1"], font_family="Consolas", selectable=True)], scroll=ft.ScrollMode.AUTO),
                                                                      height=text_h, padding=12, border_radius=8, bgcolor=C["field"], border=ft.Border.all(1, C["border"]))],
                             right=ft.TextButton("Copy to clipboard", icon=ft.Icons.CONTENT_COPY_ROUNDED, on_click=lambda e: page.run_task(copy_text, cur.structure, "Template"),
                                                 style=ft.ButtonStyle(color=C["text2"], padding=po(8, 0, 8, 0))))
        sd_right.controls = [*([ft.Text("\n".join(pstore.warnings[:3]), size=11, color=C["amber"], selectable=True)] if pstore.warnings else []), head, tp_ai_status,
                             _sd_card("SECTION MAP", [tp_blocks(tags, not compact)]), text_card]
        sd_dup_btn.content = "Duplicate to Edit" if builtin else "Duplicate"
        sd_dup_btn.on_click = lambda e: duplicate_template(cur.id)
        refresh_tp_primary()
        safe_update(sd_left, sd_right, sd_dup_btn)

    # -- custom lyrics tools ------------------------------------------------------------------------------------
    def open_tool_dialog(tid: str | None = None):
        cur = pstore.tool(tid) if tid else None
        name_tf = ft.TextField(label="Name", value=cur.name if cur else "", **field_style(height=48))
        type_dd = dd("Type", list(PS.TOOL_TYPES.items()), cur.type if cur else "snippet", lambda e: sync(), width=300)
        content_tf = ft.TextField(label="Content (the text or [tag] to insert)", value=cur.content if cur else "", **field_style(height=48))
        prefix_tf = ft.TextField(label="Prefix", value=cur.prefix if cur else "", width=150, **field_style(height=48))
        suffix_tf = ft.TextField(label="Suffix", value=cur.suffix if cur else "", width=150, **field_style(height=48))
        pair_cb = ft.Checkbox(label="With nothing selected, insert an empty pair", value=bool(cur and cur.empty_pair))
        desc_tf = ft.TextField(label="Description", value=cur.description if cur else "", **field_style(height=48))
        wrap_row = ft.Column([ft.Row([prefix_tf, suffix_tf], spacing=8), pair_cb], spacing=4)

        def sync():
            content_tf.visible = type_dd.value == "snippet"
            wrap_row.visible = type_dd.value == "wrap"
            safe_update(content_tf, wrap_row)
        content_tf.visible, wrap_row.visible = type_dd.value == "snippet", type_dd.value == "wrap"

        def save():
            pstore.save_tool(tid, name_tf.value, type_dd.value, content_tf.value, prefix_tf.value, suffix_tf.value, bool(pair_cb.value), desc_tf.value)
        open_form("Edit Custom Lyrics Tool" if cur else "New Custom Lyrics Tool", [name_tf, type_dd, content_tf, wrap_row, desc_tf,
                  ft.Text("A tag on its own line is placed like the built-in cue tools. A wrap tool needs selected text unless the empty pair is on.", size=11, color=C["text3"])], save, "Save Tool")

    # -- saved AI prompts ---------------------------------------------------------------------------------------
    def open_prompt_dialog(pid: str | None = None):
        cur = next((p for p in pstore.prompts if p.id == pid), None) if pid else None
        name_tf = ft.TextField(label="Name", value=cur.name if cur else "", **field_style(height=48))
        desc_tf = ft.TextField(label="Description", value=cur.description if cur else "", **field_style(height=48))
        prompt_tf = ft.TextField(label="Prompt", multiline=True, min_lines=5, max_lines=9, value=cur.prompt if cur else "", **field_style(height=None, text_size=12))
        scope_dd = dd("Target Scope (default)", list(PS.SCOPES.items()), cur.scope if cur else "selection", lambda e: None, width=300)

        def save():
            pstore.save_prompt(pid, name_tf.value, desc_tf.value, prompt_tf.value, scope_dd.value or "selection")
        open_form("Edit AI Prompt" if cur else "New AI Prompt", [name_tf, desc_tf, prompt_tf, scope_dd,
                  ft.Text("Placeholders: {selection} the selected text, {section} the current section name, {lyrics} the whole lyrics. The target text is always sent as context, so you rarely need them. "
                          "The scope is only a default: the real target is taken when you press Run.", size=11, color=C["text3"])], save, "Save Prompt")

    def duplicate_prompt(pid: str):
        try:
            pstore.duplicate_prompt(pid)
        except (PS.PresetError, OSError) as ex:
            toast(str(ex), C["red"]); return
        render_templates()

    def confirm_target(title: str, req: "AC.ActionRequest", what: str, go):
        """Shows the exact target before a saved prompt is sent (a line that was not selected, or the entire lyrics)."""
        shown = req.target if len(req.target) <= 600 else req.target[:600] + "…"
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Text(title), content=ft.Container(width=420, content=ft.Column([
            ft.Text(what, size=12, color=C["text2"]), ft.Container(ft.Text(shown, size=13, color=C["text1"], selectable=True), bgcolor=C["field"], border_radius=8, padding=10, border=ft.Border.all(1, C["border"])),
            ft.Text("The AI only proposes: nothing changes until you press Apply in the Chat.", size=11, color=C["text3"])], spacing=8, tight=True, scroll=ft.ScrollMode.AUTO, height=min(400, 140 + 14 * shown.count("\n")))),
            actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.FilledButton("Send", on_click=lambda e: (page.pop_dialog(), go()), style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))]))

    def run_saved_prompt(pid: str):
        """Run: capture the target NOW from the selection / caret the editor reported (the saved scope is only the default), fill the placeholders, then use the AI Actions engine."""
        p = next((x for x in pstore.prompts if x.id == pid), None)
        if p is None:
            return
        if not S.song:
            toast("Open or create a song first.", C["amber"]); return
        if S.chat_busy_song or S.an_busy:
            toast("The AI is still busy. Try again in a moment.", C["amber"]); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        song_id, text = S.song.id, lyr()
        a, b = S.sel
        sel_text = text[a:b].strip() if 0 <= a < b <= len(text) else ""
        section = RT.section_names(text).get(AC.line_no(text, a), "") if 0 <= a <= len(text) else ""
        try:
            instruction = PS.render_prompt(p.prompt, sel_text, section, text)
            req = AC.make_request("custom", text, S.sel, {"scope": p.scope, "name": p.name, "instruction": instruction, "preset_id": p.id, "prompt": p.prompt}, S.backend)
        except (PS.PresetError, AC.ActionError) as ex:
            toast(str(ex), C["amber"]); return

        def go():
            page.run_task(run_action, req, song_id)
        if req.mode == "all":
            confirm_target(p.name, req, "This prompt will work on the ENTIRE lyrics. The whole song is sent to the AI, and its proposal would replace all of it if you apply it.", go)
        elif req.mode == "line":
            confirm_target(p.name, req, "Nothing is selected, so this prompt will work on the line your cursor is in:", go)
        else:
            go()

    def tp_head(title: str, new_label: str, on_new):
        return ft.Row([tp_label(title), ft.Container(expand=True), ft.OutlinedButton(new_label, icon=ft.Icons.ADD_ROUNDED, height=30, on_click=on_new)], vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def tp_empty(icon, line1: str, line2: str):
        return ft.Container(ft.Column([ft.Icon(icon, size=26, color=C["text3"]), ft.Text(line1, size=13, color=C["text1"], weight=ft.FontWeight.W_600),
                                       ft.Text(line2, size=12, color=C["text3"], text_align=ft.TextAlign.CENTER)], spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                            padding=po(16, 20, 16, 20), border_radius=10, border=ft.Border.all(1, C["border"]), alignment=ft.Alignment(0, 0))

    def tp_row(icon, name: str, desc: str, items: list, primary=None):
        """A compact row: icon, name + one-line description, an optional primary action, and the secondary actions in an overflow menu."""
        trailing = ([primary] if primary is not None else []) + [ft.PopupMenuButton(icon=ft.Icons.MORE_VERT_ROUNDED, icon_size=18, icon_color=C["text2"], tooltip="More", items=items)]
        return ft.Container(ft.Row([ft.Icon(icon, size=18, color=C["accent"]),
                                    ft.Column([ft.Text(name, size=13, color=C["text1"], weight=ft.FontWeight.W_600, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                               ft.Text(desc or " ", size=11, color=C["text3"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)], spacing=0, expand=True), *trailing],
                                   spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER), bgcolor=C["panel2"], border_radius=8, padding=po(12, 4, 4, 4))

    def _tp_tools():
        rows = []
        for t in pstore.tools:
            what = (f"Inserts {t.content}" if t.type == "snippet" else f"Wraps the selection: {t.prefix} … {t.suffix}")
            rows.append(tp_row(ft.Icons.EXTENSION_ROUNDED, t.name, what + (f"  ·  {t.description}" if t.description else ""), [
                ft.PopupMenuItem(content=ft.Text("Edit"), on_click=lambda e, i=t.id: open_tool_dialog(i)),
                ft.PopupMenuItem(content=ft.Text("Delete…"), on_click=lambda e, i=t.id, n=t.name: ask_delete("tool", n, lambda: pstore.delete_tool(i)))]))
        return [tp_head("CUSTOM LYRICS TOOLS", "New Tool", lambda e: open_tool_dialog(None)),
                *(rows or [tp_empty(ft.Icons.EXTENSION_ROUNDED, "No custom tools yet", "A tool inserts a tag or snippet, or wraps the selected words. It appears at the bottom of Lyrics Tools.")])]

    def _tp_prompts():
        rows = []
        for p in pstore.prompts:
            rows.append(tp_row(ft.Icons.AUTO_AWESOME_ROUNDED, p.name, f"{PS.SCOPES[p.scope]}" + (f"  ·  {p.description}" if p.description else ""), [
                ft.PopupMenuItem(content=ft.Text("Edit"), on_click=lambda e, i=p.id: open_prompt_dialog(i)),
                ft.PopupMenuItem(content=ft.Text("Duplicate"), on_click=lambda e, i=p.id: duplicate_prompt(i)),
                ft.PopupMenuItem(content=ft.Text("Delete…"), on_click=lambda e, i=p.id, n=p.name: ask_delete("prompt", n, lambda: pstore.delete_prompt(i)))],
                primary=ft.FilledButton("Run", on_click=lambda e, i=p.id: run_saved_prompt(i), height=30, style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF", padding=po(14, 0, 14, 0)))))
        return [tp_head("SAVED AI PROMPTS", "New Prompt", lambda e: open_prompt_dialog(None)),
                *(rows or [tp_empty(ft.Icons.AUTO_AWESOME_ROUNDED, "No saved prompts yet", "Save an instruction you use often (for example “make this line rhyme”) and run it on your selection with one click.")])]

    TP_HELP = {"struct": "No sung lyrics: Insert Structure puts the template's section tags into this sheet (no AI, one undo step).\nWith lyrics: Arrange with AI places YOUR lines into the template. It never writes or changes lyrics, and you preview the result before anything changes.",
               "tools": "Custom tools insert a tag or snippet, or wrap the selected text. They appear at the bottom of Lyrics Tools and work like the built-in ones.",
               "prompts": "Run sends the prompt with the target you have at that moment. The answer appears in the Chat as a proposal; nothing changes until you press Apply."}

    def toggle_tp_expand(e=None):
        tp["exp"] = not tp["exp"]
        fit_workspace_height(); render_templates()

    def render_templates():
        """Redraw the Song Structures dialog when it is open (the library changed, a template was chosen / saved / deleted); otherwise only the primary action follows the sheet."""
        if _tp_dlg["open"]:
            render_structures()
        else:
            refresh_tp_primary()

    PS_DEFAULT_STRUCTURE = "[Intro]\n\n[Verse 1]\n\n[Chorus]\n\n[Bridge]\n\n[Final Chorus]\n\n[Outro]"
    render_templates(); rebuild_custom_tools()

    # ═══ Audio Timeline (Workspace -> Timeline): manual markers on ONE selected take. The take being managed (selected here) is NOT the take the player is playing: a marker is only
    # created from the player's position / only seeks when both are the same (song id + take id). Markers live in songs/<id>/timeline.json (timeline.py). ═════════════════════════════
    tl = {"take": {}, "marker": None, "dur": {}}                 # take chosen per song id · selected marker id · probed durations of takes whose measured length is unknown
    tl_data: dict = {}
    tl_take_dd = ft.Dropdown(width=230, **field_style(height=40, text_size=12))
    tl_dur = ft.Text("", size=12, color=C["text1"], weight=ft.FontWeight.W_600)
    tl_note = ft.Text("", size=11, color=C["amber"], visible=False)
    tl_pb = ft.Text("", size=12, color=C["text2"])
    tl_play_btn = ft.FilledButton("Play Selected Take", icon=ft.Icons.PLAY_ARROW_ROUNDED, height=34, on_click=lambda e: tl_play(), style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))
    tl_bar = ft.Row(spacing=0, height=30)
    tl_bar_ends = ft.Row([ft.Text("00:00", size=11, color=C["text3"]), ft.Container(expand=True), ft.Text("", size=11, color=C["text3"])])
    tl_list = ft.Column(spacing=2)                               # no scroll of its own: the Timeline view scrolls as ONE list (no nested scroll areas in a small window)
    tl_add_pos = ft.OutlinedButton("+ Add at Playback Position", height=32, on_click=lambda e: tl_add_at_position())
    tl_add_man = ft.OutlinedButton("+ Add Manually", height=32, on_click=lambda e: tl_open_dialog(None, 0))
    tl_empty = ft.Text("", size=12, color=C["text3"], visible=False)
    tl_body = ft.Column([ft.Row([tl_take_dd, tl_dur, tl_play_btn], spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True, run_spacing=4), tl_note, tl_pb,
                         tl_bar, tl_bar_ends, ft.Row([ft.Text("SECTION MARKERS", size=11, weight=ft.FontWeight.BOLD, color=C["text3"]), ft.Container(width=6), tl_add_pos, tl_add_man], spacing=8, wrap=True, run_spacing=4,
                                                     vertical_alignment=ft.CrossAxisAlignment.CENTER), tl_list], spacing=6)
    timeline_view = ft.Column([tl_empty, tl_body], spacing=6, scroll=ft.ScrollMode.AUTO)

    def tl_store(song_id: str) -> "TL.TimelineData":
        d = tl_data.get(song_id)
        if d is None:
            d = tl_data[song_id] = TL.TimelineData.load(ST.song_dir(song_id) / "timeline.json")
            for w in d.warnings:
                log.warning("timeline %s: %s", song_id, w)
        return d

    def tl_take() -> "M.Take | None":
        """The take being managed in the Timeline (chosen here, per song; the newest take until chosen). It has nothing to do with what the player is playing."""
        if not S.song:
            return None
        takes = sorted(S.takes, key=lambda t: t.number, reverse=True)
        t = next((x for x in takes if x.id == tl["take"].get(S.song.id)), None)
        if t is None and takes:
            t = takes[0]
            tl["take"][S.song.id] = t.id
        return t

    def tl_duration_ms(t: "M.Take") -> "int | None":
        """The VERIFIED length of the audio: what a probe of the real file reports (cached). Take.duration is not trusted on its own, because takes made by older versions stored the
        REQUESTED length when measuring failed; it is only a hint that is replaced by the probe. None = unknown (no file, no player, or the probe failed): nothing is invented."""
        if t.id not in tl["dur"]:
            p = ST.take_path(t)
            n = 0
            if p and player.available:
                try:
                    n = int(PlayerService.probe_length_ms(str(p)))
                except Exception:
                    n = 0
            tl["dur"][t.id] = n
        return tl["dur"][t.id] or None

    def tl_playing_same(t) -> bool:
        c = player.current
        return bool(t and c and c.song_id == t.song_id and c.take_id == t.id and player.mci.loaded)

    def tl_bar_build(markers: list, dur: int, cursor: "int | None"):
        lay = TL.bar_layout(markers, dur, cursor)
        segs = []
        for s in lay["segments"]:
            ms_here = s["markers"]
            sel = any(m.id == tl["marker"] for m in ms_here)
            border = ft.Border(left=ft.BorderSide(3 if sel else 2, C["amber"] if sel else C["accent"])) if ms_here else None
            if s["cursor_edge"]:
                border = ft.Border(left=border.left if border else None, right=ft.BorderSide(2, "#FFFFFF"))
            label = " / ".join(m.label for m in ms_here)
            segs.append(ft.Container(content=ft.Text(label, size=11, color=C["text1"], no_wrap=True, overflow=ft.TextOverflow.CLIP), expand=s["weight"], height=30,
                                     bgcolor=C["accent_soft"] if s["played"] else C["field"], border=border, padding=po(3, 2, 0, 0),
                                     tooltip=(f"{label}  ·  {TL.format_auto(ms_here[0].time_ms)}" if label else None)))
        if lay["end_markers"]:                                  # markers exactly at (or after) the end: a visible End tick, labels in its tooltip
            sel = any(m.id == tl["marker"] for m in lay["end_markers"])
            segs.append(ft.Container(width=5, height=30, bgcolor=C["amber"] if sel else C["accent"], border_radius=2,
                                     tooltip="End: " + " / ".join(f"{m.label} ({TL.format_auto(m.time_ms)})" for m in lay["end_markers"])))
        return segs

    def tl_draw_playback():
        t = tl_take()
        if not t:
            return
        dur = tl_duration_ms(t)
        same = tl_playing_same(t)
        c = player.current
        if same:
            pos = player.position_ms()
            tl_pb.value = f"Playback: {TL.format_auto(pos)} / {TL.format_auto(dur) if dur else '?'}" + ("" if player.state == "playing" else "  (paused)")
        elif c and player.state in ("playing", "paused"):
            tl_pb.value = f"The player is playing “{c.title} · {c.label}”, not the selected take."
        else:
            tl_pb.value = "Not playing"
        tl_play_btn.content = "Pause" if (same and player.state == "playing") else "Play Selected Take"
        tl_play_btn.icon = ft.Icons.PAUSE_ROUNDED if (same and player.state == "playing") else ft.Icons.PLAY_ARROW_ROUNDED
        tl_play_btn.disabled = ST.take_path(t) is None
        data = tl_store(t.song_id)
        mk = data.markers(t.id)
        if dur:
            tl_bar.controls = tl_bar_build(mk, dur, player.position_ms() if same else None)
            ends = [m for m in mk if m.time_ms >= dur]
            tl_bar_ends.controls[2].value = TL.format_auto(dur) + (f"   · marker at the end: {', '.join(m.label for m in ends)}"[:60] if ends else "")
        else:
            tl_bar.controls = [ft.Container(ft.Text("The length of this take is not known, so the bar is not drawn. Markers still work.", size=11, color=C["text3"]), padding=po(6, 6, 6, 0), expand=True, height=30)]
            tl_bar_ends.controls[2].value = ""
        safe_update(tl_pb, tl_play_btn, tl_bar, tl_bar_ends)

    def tl_tick():
        if S.ws_tab == "timeline" and S.ws_open and S.song:
            try:
                tl_draw_playback()
            except Exception as ex:
                dbg_once("timeline tick", ex)

    def refresh_timeline():
        if S.ws_tab != "timeline":
            return
        t = tl_take()
        tl_empty.visible, tl_body.visible = False, True
        if not S.song or t is None:
            tl_empty.value = "Open a song first." if not S.song else "This song has no takes yet. Generate a take first; markers belong to a take."
            tl_empty.visible, tl_body.visible = True, False
            safe_update(tl_empty, tl_body)
            return
        takes = sorted(S.takes, key=lambda x: x.number, reverse=True)
        tl_take_dd.options = [ft.dropdown.Option(x.id, f"Take {x.number:02d}" + ("" if ST.take_path(x) else "  (audio missing)")) for x in takes]
        tl_take_dd.value = t.id
        tl_take_dd.on_select = synced(tl_pick_take)
        if not tl["dur"].get(t.id):
            tl["dur"].pop(t.id, None)                                   # a failed probe is tried again whenever the Timeline is refreshed (not on every tick)
        dur = tl_duration_ms(t)
        tl_dur.value = f"Duration: {TL.format_auto(dur)}" if dur else "Duration: Unknown"
        data = tl_store(t.song_id)
        notes = []
        if ST.take_path(t) is None:
            notes.append("The audio file of this take is missing: your markers are kept; playback and seek are unavailable.")
        if not TL.section_refs(t.lyrics):
            notes.append("This take has no generated-lyrics snapshot, so only custom markers can be added (no section references are guessed).")
        if not dur:
            notes.append("The real length of the audio is not known: times cannot be checked against the end.")
        notes += data.warnings[:2]
        tl_note.value, tl_note.visible = "\n".join(notes), bool(notes)
        refs = TL.section_refs(t.lyrics)
        rows = []
        for m in data.markers(t.id):
            sel = tl["marker"] == m.id
            ref = TL.find_ref(refs, m.section_ref)
            tag = f"  ·  section: {ref.label}" if ref else ("  ·  section (not in this take's lyrics)" if m.section_ref else "")
            rows.append(ft.Container(ft.Row([ft.Text(TL.format_auto(m.time_ms), size=12, color=C["accent"], font_family="Consolas", width=76),
                                             ft.Text(m.label + tag, size=12, color=C["text1"], expand=True, selectable=False, weight=ft.FontWeight.W_700 if sel else None),   # the full label (it wraps), never cut off
                                             tp_icon(ft.Icons.EDIT_OUTLINED, "Edit", lambda i=m.id: tl_open_dialog(i, 0)),
                                             tp_icon(ft.Icons.DELETE_OUTLINE_ROUNDED, "Delete", lambda i=m.id: tl_delete(i))], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                                     padding=po(8, 2, 2, 2), border_radius=6, ink=True, bgcolor=C["accent_soft"] if sel else None, tooltip="Click: jump to this time (only when this take is the one playing)",
                                     on_click=lambda e, i=m.id: tl_click_marker(i)))
        tl_list.controls = rows or [ft.Text("No markers yet. Use “Add at Playback Position” while the take plays, or “Add Manually”.", size=11, color=C["text3"])]
        tl_add_pos.disabled = ST.take_path(t) is None
        safe_update(tl_take_dd, tl_dur, tl_note, tl_list, tl_add_pos, tl_empty, tl_body)
        tl_draw_playback()

    def tl_pick_take(e):
        if S.song:
            tl["take"][S.song.id] = e.control.value               # only the Timeline's own choice: the player and its queue are not touched
            tl["marker"] = None
            refresh_timeline()

    def tl_play():
        t = tl_take()
        if not (S.song and t):
            return
        takes_asc = sorted(S.takes, key=lambda x: x.number)
        play_take(S.song.id, S.song.title, t, lambda: queue_for_song(S.song.id, takes_asc))        # the user's own command: the normal play path
        tl_draw_playback()

    def tl_click_marker(mid: str):
        t = tl_take()
        if not t:
            return
        m = tl_store(t.song_id).get(t.id, mid)
        if m is None:
            return
        tl["marker"] = mid
        c = player.current
        why = TL.seek_allowed(t.song_id, t.id, c.song_id if c else "", c.take_id if c else "", player.mci.loaded)
        if why:
            toast(why, C["amber"])
        else:
            err = player.seek_ms(m.time_ms, t.song_id, t.id)
            if err:
                toast(err, C["red"])
        refresh_timeline()

    def tl_add_at_position():
        t = tl_take()
        if not t:
            return
        c = player.current
        why = TL.seek_allowed(t.song_id, t.id, c.song_id if c else "", c.take_id if c else "", player.mci.loaded)
        if why:
            toast("Cannot use the playback position: " + why, C["amber"]); return
        if player.state not in ("playing", "paused"):
            toast("The take is not playing. Start it with “Play Selected Take”, or use “Add Manually”.", C["amber"]); return
        p = ST.take_path(t)
        if p is None:
            toast("The audio file of this take is missing.", C["red"]); return
        pos, dur = player.position_ms(), tl_duration_ms(t)
        if pos < 0 or (dur and pos > dur):
            toast("The player reports a position outside this take. Nothing was added.", C["red"]); return
        tl_open_dialog(None, pos)

    def tl_open_dialog(marker_id: "str | None", prefill_ms: int):
        t = tl_take()
        if not (S.song and t):
            return
        song_id, take_id = S.song.id, t.id
        data = tl_store(song_id)
        rev = data.rev(take_id)
        dur = tl_duration_ms(t)
        refs = TL.section_refs(t.lyrics)                            # the TAKE's own lyrics snapshot, never the editor's current lyrics
        cur = data.get(take_id, marker_id) if marker_id else None
        if marker_id and cur is None:
            toast("That marker no longer exists.", C["amber"]); return
        opts = [(r.ref, r.label) for r in refs]
        if cur and cur.section_ref and TL.find_ref(refs, cur.section_ref) is None:
            opts.append((cur.section_ref, f"{cur.section_ref} (not in this take's lyrics)"))
        start_type = "section" if (cur and cur.section_ref) else ("section" if (not cur and opts) else "custom")
        type_dd = dd("Type", [("section", "Section Marker"), ("custom", "Custom Marker")], start_type, lambda e: sync(), width=300)
        sec_dd = dd("Section", opts, (cur.section_ref if cur and cur.section_ref else (opts[0][0] if opts else None)), lambda e: pick_section(), width=300)
        label_tf = ft.TextField(label="Name", value=cur.label if cur else (opts[0][1] if opts and start_type == "section" else ""), **field_style(height=48))
        time_tf = ft.TextField(label="Time (mm:ss or mm:ss.mmm)", value=TL.format_time(cur.time_ms if cur else prefill_ms, True), width=200, **field_style(height=48))
        auto = {"label": label_tf.value}
        hint = ft.Text((f"Take {t.number:02d} · duration {TL.format_auto(dur)}: later times are refused." if dur else f"Take {t.number:02d} · the real length is not known, so only negative times are refused.")
                       + ("" if opts else "\nThis take has no generated-lyrics snapshot: only custom markers are possible."), size=11, color=C["text3"])

        def sync():
            sec_dd.visible = type_dd.value == "section" and bool(opts)
            safe_update(sec_dd)
            if type_dd.value == "section" and opts:
                pick_section()

        def pick_section():
            lab = dict(opts).get(sec_dd.value, "")
            if lab and label_tf.value.strip() in ("", auto["label"]):
                label_tf.value = auto["label"] = lab
                safe_update(label_tf)
        if not opts:
            type_dd.value = "custom"
            type_dd.disabled = True
        sec_dd.visible = type_dd.value == "section" and bool(opts)

        def save():
            if not (S.song and S.song.id == song_id and tl_take() is not None and tl_take().id == take_id):
                raise TL.TimelineError("The song or the selected take changed while the dialog was open. Nothing was saved.")
            ms = TL.parse_time(time_tf.value)
            ref = sec_dd.value if (type_dd.value == "section" and opts) else None
            if cur is None:
                m = data.add(take_id, ms, label_tf.value, ref, dur, rev)
            else:
                m = data.update(take_id, cur.id, ms, label_tf.value, ref, dur, rev)
            tl["marker"] = m.id
        open_form("Edit Marker" if cur else "Add Marker", [ft.Text(f"Take: Take {t.number:02d}", size=12, color=C["text2"]), type_dd, sec_dd, label_tf, time_tf, hint], save,
                  "Save Marker" if cur else "Add Marker", after=refresh_timeline)

    def tl_delete(mid: str):
        t = tl_take()
        if not (S.song and t):
            return
        song_id, take_id = S.song.id, t.id
        data = tl_store(song_id)
        m = data.get(take_id, mid)
        if m is None:
            return
        rev = data.rev(take_id)

        def do():
            if not (S.song and S.song.id == song_id and tl_take() is not None and tl_take().id == take_id):
                toast("The song or take changed. Nothing was deleted.", C["amber"]); return
            try:
                data.delete(take_id, mid, rev)
            except (TL.TimelineError, OSError) as ex:
                toast(str(ex), C["red"]); return
            if tl["marker"] == mid:
                tl["marker"] = None
            refresh_timeline()
        confirm("Delete marker", f"Delete the marker “{m.label}” at {TL.format_auto(m.time_ms)}? The audio is not changed.", "Delete", do, danger=True)

    _tl_hook["fn"], _tl_hook["tick"] = refresh_timeline, tl_tick

    def refresh_ws():
        return                                                         # the Workspace is not shown any more
        if S.ws_tab not in dict(WS_TABS):
            S.ws_tab = "actions"                                           # a tab that no longer exists (Templates) opens AI Actions
        ws_tabs_row.controls = seg_buttons(WS_TABS, S.ws_tab, set_ws_tab)
        ws_content.content = lyrics_ws_view if S.ws_tab == "lyrics" else (actions_view if S.ws_tab == "actions" else (tools_view if S.ws_tab == "tools" else (preview_view if S.ws_tab == "preview" else (timeline_view if S.ws_tab == "timeline" else coming_soon(WS_BODY[S.ws_tab])))))
        if S.ws_tab == "lyrics":
            refresh_lyrics_ws()
        if S.ws_tab == "timeline":
            refresh_timeline()
        ws_content.visible = S.ws_open
        ws_divider.visible = S.ws_open
        if S.ws_tab == "preview" and S.ws_open:
            pv_follow(force=True)
        if S.ws_tab == "tools" and S.ws_open:
            tools_layout()
            refresh_context(force=True)                                    # the tab shows the tools for the caret as it is now
        btn_ws.icon = ft.Icons.EXPAND_MORE_ROUNDED if S.ws_open else ft.Icons.EXPAND_LESS_ROUNDED
        btn_ws.tooltip = "Collapse Workspace" if S.ws_open else "Expand Workspace"
        safe_update(ws_tabs_row, ws_content, ws_divider, btn_ws)

    def set_ws_tab(k: str):
        S.ws_tab = k
        refresh_ws(); fit_workspace_height(); persist_layout()

    def toggle_ws():
        S.ws_open = not S.ws_open
        refresh_ws(); fit_workspace_height(); persist_layout()

    def fit_workspace_height():
        return
        """The workspace never takes so much height that the lyrics editor is left with less than ~300 px; Timeline gets more room than the other tabs."""
        if not S.ws_open:
            workspace_holder.height = 52
        else:
            room = page_h() - 56 - 64 - 20 - 8 - 300                 # tab bar, player, page padding, divider, the minimum editor
            want = max(S.ws_h, 320 if S.ws_tab == "timeline" else (240 if pv_mode["wide"] else 330)) if S.ws_tab in ("timeline", "preview") else S.ws_h          # the Lyrics Tools tab keeps the height the user chose: it scrolls, the editor is not squeezed
            workspace_holder.height = max(100, min(want, room))
        safe_update(workspace_holder)

    def on_ws_drag(e):
        S.ws_h = max(100, min(420, S.ws_h - e.local_delta.y))
        fit_workspace_height()

    ws_divider = divider_h(on_ws_drag, lambda e: persist_layout())       # saved once, when the divider is let go (not on every mouse move)
    center_col_ui = ft.Column([top_row], spacing=0, expand=True)                  # no Workspace under the editor any more

    # Right Panel: three tabs (Lyrics Tools | Analysis | Generate) and the Output takes below. In Generate, Basic / Advanced / AI Music Director are a small navigation: a menu, and the chosen
    # page slides in from the right (← Back). The Generate button, the caption and the status stay under both, so Generate is one tap away. Opening a page starts nothing.
    RP_ANIM = ft.Animation(220, ft.AnimationCurve.EASE_OUT_CUBIC)
    RP_TABS = [("tools", "Lyrics Tools"), ("rep_lyrics", "Lyric Analysis"), ("rep_vocal", "Vocal Analysis"), ("cc_rhyme", "Rhyme & Syllables"), ("cc_rhythm", "Rhythm & Duration"), ("gen", "Generate"), ("settings", "Song Settings"), ("results", "Results")]                  # Repaint is a center tab beside Lyrics, not a sidebar page
    RP_MODES = {"basic": ("Basic", "Choose chips; the caption is built from them (no AI)", ft.Icons.TUNE_ROUNDED),
                "advanced": ("Advanced", "Write and control the music caption yourself", ft.Icons.EDIT_NOTE_ROUNDED),
                "ai": ("AI Music Director", "Suggest style, BPM and key from the lyrics", ft.Icons.AUTO_AWESOME_ROUNDED)}

    def rp_item(key: str):
        title, desc, icon = RP_MODES[key]
        on = key == (S.song.settings.style_mode if S.song else "basic")
        return ft.Container(ft.Row([ft.Icon(icon, size=18, color=C["accent"] if on else C["text2"]),
                                    ft.Column([ft.Text(title, size=12, weight=ft.FontWeight.W_600, color=C["text1"]),
                                               ft.Text(desc, size=10, color=C["text3"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)], spacing=0, expand=True),
                                    ft.Icon(ft.Icons.CHEVRON_RIGHT_ROUNDED, size=16, color=C["text3"])], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            padding=po(10, 6, 8, 6), border_radius=8, bgcolor=C["field"], ink=True, on_click=lambda e, k=key: rp_open_page(k),
                            border=ft.Border.all(1, C["accent"] if on else C["border"]))

    rp_menu_col = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, expand=True)

    def rp_build_menu():
        rp_menu_col.controls = [ft.Container(ft.Text("CAPTION SOURCE", size=10, weight=ft.FontWeight.W_600, color=C["text3"]), padding=po(2, 2, 0, 0))] + [rp_item(k) for k in ("basic", "advanced", "ai")]
        safe_update(rp_menu_col)
    rp_build_menu()
    rp_menu_layer = ft.Container(rp_menu_col, left=0, top=0, right=0, bottom=0, offset=ft.Offset(0, 0), animate_offset=RP_ANIM, padding=po(0, 0, 4, 0))
    rp_detail_layer = ft.Container(ft.Column([settings_col], spacing=8, scroll=ft.ScrollMode.AUTO), left=0, top=0, right=0, bottom=0, offset=ft.Offset(1, 0), animate_offset=RP_ANIM)
    gen_slide = ft.Container(ft.Stack([rp_menu_layer, rp_detail_layer], expand=True), expand=True, clip_behavior=ft.ClipBehavior.HARD_EDGE)
    # dr_regen_btn / dr_restore_btn (AI Music Director regenerate / restore) are hidden for now: they are not in the layout
    gen_page = ft.Container(panel(ft.Column([gen_slide, ft.Row([btn_generate, btn_cancel_gen], spacing=6), cap_card, gen_status], spacing=8), padding=12),
                            left=0, top=0, right=0, bottom=0, visible=False)
    settings_page = ft.Container(panel(ft.Column([ft.Text("Workflow and generation settings of this song", size=11, color=C["text3"]), shared_box, adv_tile], spacing=8, scroll=ft.ScrollMode.AUTO, expand=True), padding=12, expand=True),
                                 left=0, top=0, right=0, bottom=0, visible=False)
    tools_page = ft.Container(panel(ft.Column([tools_view], spacing=0, expand=True), padding=10), left=0, top=0, right=0, bottom=0)
    an_page = ft.Container(panel(analysis_view, padding=12, expand=True), left=0, top=0, right=0, bottom=0, visible=False)
    # The Report page: one panel per assistant (the Songwriter AI, which hands in the rhyme report with its own, and the Music Producer AI). Each has its own button and runs on its own, one at a time.
    report_run_btn = ft.FilledButton("", icon=ft.Icons.INSIGHTS_ROUNDED, height=36, on_click=lambda e: start_report(report_view["k"]),
                                     style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF", shape=ft.RoundedRectangleBorder(radius=8)))
    REPORT_TIP = {"songwriter": "The Songwriter AI reads the whole song (lyrics, meaning, flow) and hands in the rhyme report with its own report. Suggestions you can apply",
                  "music_producer": "The Music Producer AI reads the whole song (structure, cues, hook, arrangement, delivery) and writes its own report. Suggestions you can apply"}

    def report_pick(k: str):
        report_view["k"] = k
        report_run_btn.content = "Write the report"
        report_run_btn.tooltip = REPORT_TIP.get(k, "")
        rh_view.visible = True
        safe_update(report_run_btn)
        render_review()
        if k == "songwriter":
            render_rhymes()
    report_run_btn.content = "Write the report"
    report_run_btn.tooltip = REPORT_TIP["songwriter"]
    report_page = ft.Container(ft.Column([ft.Row([report_run_btn], spacing=4), review_box], spacing=6, expand=True), left=0, top=0, right=0, bottom=0, visible=False)       # the Lyric Analysis / the Vocal Analysis (one report each) of the Right Panel
    output_panel = panel(ft.Column([ft.Row([output_title, ft.Container(expand=True), output_count], vertical_alignment=ft.CrossAxisAlignment.CENTER), ft.Container(output_list, expand=True)], spacing=8, expand=True),
                         padding=12, expand=True)
    results_page = ft.Container(output_panel, left=0, top=0, right=0, bottom=0, visible=False)                # the takes of this song (rate, play, export) are a tab of their own

    # Repaint: one tab. The source take is never overwritten; the result is a new take of the song open in the editor.
    RP = {"song_id": "", "take_id": "", "path": "", "token": 0, "duration": 0.0, "wave": None, "view_start": 0.0, "view_end": 0.0,
          "sel_start": 0.0, "sel_end": 0.0, "zoom": 1.0, "width": 280, "png_n": 0, "png": None, "pending_draft": None, "anchor": None}
    # Center of the editor: Lyrics stays, and each Repaint source is another tab. The live controls show the active tab.
    CT = {"active": "lyrics", "seq": 0, "tabs": [], "row": None, "lyrics": None, "page": None}
    rp_lock = {"on": False}
    rp_dest = ft.Text("Open a song first. The new take is added to the song in the editor.", size=11, color=C["text3"])
    rp_empty = ft.Text("No takes with audio in this library.", size=12, color=C["text3"], visible=False)
    rp_song_dd = dd("Source song", [], None, None)
    rp_take_dd = dd("Source take", [], None, None)
    rp_song_dd.expand = rp_take_dd.expand = True
    rp_range = ft.Text("", size=11, color=C["text3"])
    rp_wave_msg = ft.Text("", size=11, color=C["amber"], visible=False)
    rp_start = tfield("Start (s)", "0.00", None, number=True)
    rp_end = tfield("End (s)", "5.00", None, number=True)
    rp_start.expand = rp_end.expand = True
    rp_caption = ft.TextField(label="Caption", hint_text="What should replace the selected section", multiline=True, min_lines=3, max_lines=6,
                              **field_style(expand=True, height=None))
    rp_wave_img = ft.Image(src="", width=280, height=96, fit=ft.BoxFit.FILL, visible=False)
    rp_wave_sel = ft.Container(left=0, top=0, width=0, height=96, bgcolor="#402B73FF", border=ft.Border.all(1, "#2B73FF"), visible=False)
    rp_wave_stack = ft.Stack([ft.Container(width=280, height=96, bgcolor=C["field"], border_radius=8), rp_wave_img, rp_wave_sel], width=280, height=96)

    def rp_audio_library():
        out = []
        for x in S.lib:
            takes = []
            for t in sorted(x["takes"], key=lambda t: t.number):
                p = ST.take_path(t)
                if p:
                    takes.append((t.id, f"Take {t.number:02d}", str(p)))
            if takes:
                out.append((x["song"].id, x["song"].title, takes))
        return out

    def rp_song_title(song_id: str) -> str:
        if S.song and S.song.id == song_id:
            return S.song.title
        entry = lib_entry(song_id)
        return entry["song"].title if entry else "Song"

    def rp_save_draft():
        if not RP["song_id"] or not RP["take_id"]:
            return
        try:
            RD.put_draft(ST.library_dir(), KJ.PROJECT_ID, RP["song_id"], RP["take_id"], {
                "caption": rp_caption.value or "", "start": RP["sel_start"], "end": RP["sel_end"], "zoom": RP["zoom"],
                "view_start": RP["view_start"], "view_end": RP["view_end"]})
        except Exception as ex:
            dbg_once("repaint draft", ex)

    def rp_place_overlay():
        left, wid = WF.selection_rect(RP["width"], RP["view_start"], RP["view_end"], RP["sel_start"], RP["sel_end"])
        rp_wave_sel.left, rp_wave_sel.width = left, max(2.0, wid) if wid else 0
        rp_wave_sel.visible = bool(wid) and RP["duration"] > 0
        if RP["duration"] > 0:
            rp_range.value = f"{RP['sel_start']:.2f}–{RP['sel_end']:.2f} s of {RP['duration']:.2f} s"
        else:
            rp_range.value = ""
        safe_update(rp_wave_sel, rp_range)

    def rp_write_times():
        rp_lock["on"] = True
        rp_start.value, rp_end.value = f"{RP['sel_start']:.2f}", f"{RP['sel_end']:.2f}"
        rp_start.error_text = rp_end.error_text = None
        rp_lock["on"] = False
        safe_update(rp_start, rp_end)

    def rp_wave_width() -> int:
        """The waveform fills the center editor column, which is where the Repaint tab sits."""
        try:
            return max(160, int(ws_width()) - 48)
        except Exception:
            return 480

    async def rp_paint(token: int):
        if token != RP["token"]:
            return
        w = rp_wave_width()
        RP["width"] = w
        rp_wave_stack.width = rp_wave_img.width = w
        if RP["wave"] is None:
            rp_wave_img.visible = False
            rp_place_overlay()
            safe_update(rp_wave_stack, rp_wave_img, rp_wave_msg, rp_start, rp_end)
            return
        mins, maxs = WF.visible_peaks(RP["wave"], RP["view_start"], RP["view_end"], w)
        RP["png_n"] += 1
        dest = Path(tempfile.gettempdir()) / "workflowpad_waves" / f"w{token}_{RP['png_n']}.png"
        try:
            await asyncio.get_running_loop().run_in_executor(None, lambda: WF.render_png(mins, maxs, dest, w, 96))
        except Exception as ex:
            if token != RP["token"]:
                return
            rp_wave_msg.value, rp_wave_msg.visible = str(ex), True
            safe_update(rp_wave_msg)
            return
        if token != RP["token"]:
            return
        prev = RP.get("png")
        RP["png"] = dest
        rp_wave_img.src, rp_wave_img.visible = str(dest), True
        rp_place_overlay()
        safe_update(rp_wave_stack, rp_wave_img, rp_wave_msg, rp_start, rp_end)
        if prev and prev != dest:
            try:
                Path(prev).unlink()
            except Exception:
                pass

    async def rp_load_wave(token: int, path: str):
        loop = asyncio.get_running_loop()
        wave, err = None, ""
        if path and Path(path).is_file():
            try:
                wave = await loop.run_in_executor(None, lambda: WF.load_peaks(path, cache_dir=WF.default_cache_dir()))
            except Exception as ex:
                err = str(ex)
        owner = next((t for t in CT["tabs"] if t.get("token") == token), None)
        if owner is None and token != RP["token"]:
            return
        dur = float(wave.duration) if wave is not None else 0.0
        if dur <= 0 and path and Path(path).is_file():
            try:
                ms = await loop.run_in_executor(None, PlayerService.probe_length_ms, path)
                dur = (ms / 1000.0) if ms else 0.0
            except Exception:
                dur = 0.0
        draft = owner.get("pending_draft") if owner is not None else None
        if owner is not None:
            owner["pending_draft"] = None
        if draft is None and token == RP["token"]:
            draft = RP.get("pending_draft")
        RP["pending_draft"] = None
        if isinstance(draft, dict):
            caption = str(draft.get("caption") or "")
            sel_start = float(draft.get("start", 0) or 0)
            sel_end = float(draft.get("end", min(5.0, dur or 5.0)) or 0)
            zoom = max(1.0, float(draft.get("zoom", 1) or 1))
            view = (float(draft.get("view_start") or 0), float(draft.get("view_end") or dur)) if draft.get("view_end") else None
        else:
            caption, sel_start, sel_end, zoom, view = "", 0.0, (min(5.0, dur) if dur else 0.0), 1.0, None
        if dur:
            sel_start = min(max(0.0, sel_start), dur)
            sel_end = min(max(sel_start, sel_end), dur)
        mid = (sel_start + sel_end) / 2.0
        if view is None:
            view = WF.view_window(dur, zoom, mid)
        if err:
            msg, msg_on = err, True
        elif not path or not Path(path).is_file():
            msg, msg_on = "The source audio file is missing.", True
        elif wave is None and dur:
            msg, msg_on = "Could not draw the waveform. The times below use the file length.", True
        else:
            msg, msg_on = "", False
        snap = {"song_id": owner["song_id"] if owner is not None else RP["song_id"],
                "take_id": owner["take_id"] if owner is not None else RP["take_id"],
                "path": path, "duration": dur, "wave": wave, "view_start": view[0], "view_end": view[1],
                "sel_start": sel_start, "sel_end": sel_end, "zoom": zoom, "caption": caption,
                "msg": msg, "msg_on": msg_on, "token": token}
        if owner is not None:
            owner["loading"] = False
            owner["snap"] = snap
        live = token == RP["token"] and (owner is None or CT["active"] == owner["id"])
        if not live:
            if snap["song_id"] and snap["take_id"]:
                try:
                    RD.put_draft(ST.library_dir(), KJ.PROJECT_ID, snap["song_id"], snap["take_id"], {
                        "caption": caption, "start": sel_start, "end": sel_end, "zoom": zoom,
                        "view_start": view[0], "view_end": view[1]})
                except Exception as ex:
                    dbg_once("repaint draft", ex)
            return
        RP["wave"], RP["duration"] = wave, dur
        RP["sel_start"], RP["sel_end"], RP["zoom"] = sel_start, sel_end, zoom
        RP["view_start"], RP["view_end"] = view
        rp_caption.value = caption
        rp_wave_msg.value, rp_wave_msg.visible = msg, msg_on
        rp_write_times()
        safe_update(rp_caption, rp_wave_msg)
        await rp_paint(token)
        if token == RP["token"]:
            rp_save_draft()

    def rp_apply_zoom(zoom: float):
        RP["zoom"] = max(1.0, min(32.0, float(zoom)))
        center = (RP["sel_start"] + RP["sel_end"]) / 2.0
        RP["view_start"], RP["view_end"] = WF.view_window(RP["duration"], RP["zoom"], center)
        rp_save_draft()
        page.run_task(rp_paint, RP["token"])

    def rp_on_time(which: str):
        def h(e):
            if rp_lock["on"]:
                return
            try:
                v = float((e.control.value or "").strip())
            except ValueError:
                e.control.error_text = "Seconds"
                safe_update(e.control)
                return
            e.control.error_text = None
            if which == "start":
                RP["sel_start"] = max(0.0, v)
            else:
                RP["sel_end"] = v
            inside = RP["duration"] <= 0 or (RP["sel_start"] >= RP["view_start"] - 1e-3 and RP["sel_end"] <= RP["view_end"] + 1e-3)
            if inside:
                rp_place_overlay()
                rp_save_draft()
            else:
                rp_apply_zoom(RP["zoom"])
            safe_update(e.control)
        return h

    rp_start.on_change, rp_end.on_change = rp_on_time("start"), rp_on_time("end")
    rp_caption.on_change = lambda e: rp_save_draft()

    def _rp_x(e) -> float:
        pos = getattr(e, "local_position", None)
        return float(pos.x if pos is not None else 0)

    def rp_pan_start(e):
        if RP["duration"] <= 0:
            return
        RP["anchor"] = WF.x_to_time(_rp_x(e), RP["width"], RP["view_start"], RP["view_end"])
        RP["sel_start"] = RP["sel_end"] = RP["anchor"]
        rp_write_times(); rp_place_overlay()

    def rp_pan_update(e):
        if RP.get("anchor") is None or RP["duration"] <= 0:
            return
        t = WF.x_to_time(_rp_x(e), RP["width"], RP["view_start"], RP["view_end"])
        a, b = float(RP["anchor"]), t
        if b < a:
            a, b = b, a
        if b <= a:
            b = min(RP["duration"], a + 0.05)
        RP["sel_start"], RP["sel_end"] = a, min(b, RP["duration"])
        rp_write_times(); rp_place_overlay()

    def rp_pan_end(e=None):
        RP["anchor"] = None
        rp_save_draft()

    rp_wave_gd = ft.GestureDetector(content=rp_wave_stack, drag_interval=16, on_pan_start=rp_pan_start, on_pan_update=rp_pan_update, on_pan_end=rp_pan_end, on_pan_cancel=rp_pan_end)

    def rp_toggle(e=None):
        _repaint_play["until"] = None
        if not RP["path"] or not Path(RP["path"]).is_file():
            toast("The source audio file is missing.", C["red"]); return
        label = next((lab for _i, _n, takes in rp_audio_library() for tid, lab, _p in takes if tid == RP["take_id"]), "Take")
        player.play_take(QItem(RP["song_id"], RP["take_id"], rp_song_title(RP["song_id"]), label, RP["path"]), None)
        if player.error:
            toast(f"Cannot play: {player.error}", C["red"])

    def rp_play_selection(e=None):
        if not RP["path"] or not Path(RP["path"]).is_file():
            toast("The source audio file is missing.", C["red"]); return
        if RP["sel_end"] <= RP["sel_start"]:
            toast("Choose a selection first.", C["amber"]); return
        label = next((lab for _i, _n, takes in rp_audio_library() for tid, lab, _p in takes if tid == RP["take_id"]), "Take")
        item = QItem(RP["song_id"], RP["take_id"], rp_song_title(RP["song_id"]), label, RP["path"])
        if not player.is_current(RP["take_id"]):
            player.play_take(item, [item])
        err = player.seek_ms(int(RP["sel_start"] * 1000), RP["song_id"], RP["take_id"])
        if err:
            toast(err, C["red"]); return
        if player.state != "playing":
            player.toggle()
        _repaint_play["until"] = (RP["song_id"], RP["take_id"], int(RP["sel_end"] * 1000))

    def select_repaint_source(song_id: str, take_id: str, load_draft: bool = True):
        if not song_id or not take_id:
            return
        current = ct_current()
        other = next((t for t in CT["tabs"] if t is not current and t["song_id"] == song_id and t["take_id"] == take_id), None)
        if other is not None:
            ct_show(other["id"])
            return
        prev = (RP["song_id"], RP["take_id"])
        if prev[1] and prev != (song_id, take_id):
            rp_save_draft()
        same = prev == (song_id, take_id) and bool(RP.get("path"))
        if same and not load_draft:
            return
        choices = rp_audio_library()
        takes = next((ts for i, _n, ts in choices if i == song_id), [])
        path = next((p for tid, _l, p in takes if tid == take_id), "")
        if not path:
            found = resolve_take_file(song_id, take_id)
            path = str(found) if found else ""
        RP["song_id"], RP["take_id"], RP["path"] = song_id, take_id, path
        rp_lock["on"] = True
        rp_song_dd.value = song_id
        rp_take_dd.options = [ft.dropdown.Option(tid, lab) for tid, lab, _p in takes]
        rp_take_dd.value = take_id if any(tid == take_id for tid, _l, _p in takes) else take_id
        rp_lock["on"] = False
        safe_update(rp_song_dd, rp_take_dd)
        if same:
            return
        RP["token"] += 1
        RP["wave"] = None
        RP["pending_draft"] = RD.get_draft(ST.library_dir(), KJ.PROJECT_ID, song_id, take_id) if load_draft else None
        tab = ct_current()
        if tab is not None:
            tab["song_id"], tab["take_id"] = song_id, take_id
            tab["token"] = RP["token"]
            tab["loading"] = True
            tab["pending_draft"] = RP["pending_draft"]
            ct_render()
        page.run_task(rp_load_wave, RP["token"], path)

    def on_rp_song(e):
        if rp_lock["on"]:
            return
        sid = e.control.value or ""
        takes = next((ts for i, _n, ts in rp_audio_library() if i == sid), [])
        select_repaint_source(sid, takes[0][0] if takes else "", load_draft=True)

    def on_rp_take(e):
        if rp_lock["on"]:
            return
        select_repaint_source(rp_song_dd.value or RP["song_id"], e.control.value or "", load_draft=True)

    rp_song_dd.on_select, rp_take_dd.on_select = synced(on_rp_song), synced(on_rp_take)

    def refresh_repaint_sources():
        """New takes show up in the lists. The open draft is kept when the source take is still there."""
        choices = rp_audio_library()
        rp_lock["on"] = True
        rp_song_dd.options = [ft.dropdown.Option(i, name) for i, name, _ts in choices]
        if RP["song_id"] and any(i == RP["song_id"] for i, _n, _ts in choices):
            rp_song_dd.value = RP["song_id"]
        takes = next((ts for i, _n, ts in choices if i == RP["song_id"]), [])
        rp_take_dd.options = [ft.dropdown.Option(tid, lab) for tid, lab, _p in takes]
        if any(tid == RP["take_id"] for tid, _l, _p in takes):
            rp_take_dd.value = RP["take_id"]
        rp_empty.visible = not choices
        rp_song_dd.visible = rp_take_dd.visible = bool(choices)
        rp_dest.value = f"New take goes to: {S.song.title}" if S.song else "Open a song first. The new take is added to the song in the editor."
        rp_lock["on"] = False
        safe_update(rp_song_dd, rp_take_dd, rp_empty, rp_dest)
        ct_render()

    def open_repaint_from(song_id: str, take):
        """Results → Repaint. Opens a center tab beside Lyrics. The same take focuses its tab; another take adds one."""
        if not FF.ENABLE_REPAINT:
            return
        if not (S.song and S.song.id == song_id):
            open_song(song_id)
        if S.tab != "editor":
            set_tab("editor")
        ct_open(song_id, take.id)

    async def do_repaint(e=None):
        await guarded_generation(_repaint_body, "Repaint")

    async def _repaint_body(own):
        if not FF.ENABLE_REPAINT:
            return
        if not S.song:
            toast("Open a song first. The new take is added to the song in the editor.", C["amber"]); return
        if S.gen and S.gen.get("status") in ("captioning", "queued", "running", "cancelling"):
            toast("A song is already being generated"); return
        path = RP.get("path") or ""
        caption = (rp_caption.value or "").strip()
        err = WF.validate_repaint(path_exists=bool(path) and Path(path).is_file(), duration=RP["duration"], start=RP["sel_start"], end=RP["sel_end"], caption=caption)
        if err:
            toast(err, C["red"]); return
        rp_save_draft()
        song = S.song
        SH.flush(song)
        snap = M.song_from_dict(M.song_to_dict(song))
        sheet = SH.active_sheet(song)
        before = SH.settings_to_dict(song.settings)
        song_id, title = snap.id, snap.title
        src_song, src_take = RP["song_id"], RP["take_id"]
        start, end, dur = float(RP["sel_start"]), float(RP["sel_end"]), float(RP["duration"])
        kept = (src_song, src_take)
        S.gen = own["gen"] = {"song_id": song_id, "status": "queued", "pid": "", "cancel": False, "note": "Uploading source audio…"}
        update_generate_button(); rebuild_output()
        rp_show_tab("results")
        gen = S.gen
        loop = asyncio.get_running_loop()
        try:
            wf, used = await loop.run_in_executor(None, lambda: KJ.prepare_repaint(
                AUDIO_API_DIR, snap.settings, host=S.host, audio_path=path, caption=caption, start=start, end=end, duration=dur,
                source_song_id=src_song, source_take_id=src_take))
        except Exception as ex:
            S.gen = None
            update_generate_button(); rebuild_output()
            toast(f"Cannot prepare the repaint: {ex}", C["red"]); return
        if gen.get("cancel"):
            S.gen = None
            update_generate_button(); rebuild_output()
            toast("Generation cancelled", C["amber"]); return
        gen_snapshot = TS.snapshot_extra(sheet.id, sheet.name, song.lyrics, before, "")
        gen["note"] = ""

        def on_state(st):
            if st == "unreachable":
                gen["note"] = "ComfyUI is not responding…"
                return
            gen["note"] = ""
            if st == "running" and gen["status"] != "cancelling":
                gen["status"] = "running"
        res = await loop.run_in_executor(None, lambda: KJ.execute(
            S.host, snap, wf, used, cancel_fn=lambda: gen["cancel"], on_state=on_state,
            on_submitted=lambda pid: gen.update(pid=pid), snapshot=gen_snapshot, lyrics="", tags=caption))
        release_gen(gen)
        refresh_lib()
        if S.song and S.song.id == song_id:
            S.takes, S.runs = ST.load_takes(song_id)
        update_generate_button(); rebuild_output(); rebuild_song_list(); rebuild_lib_page()
        run_ms_hooks()
        if (RP["song_id"], RP["take_id"]) != kept:
            select_repaint_source(kept[0], kept[1], load_draft=True)
        if res.status == "completed" and res.take is not None:
            toast(f"“{title}”: Take {res.take.number:02d} is ready", C["green"])
        elif res.status == "cancelled":
            toast("Generation cancelled", C["amber"])
        else:
            toast(f"Repaint failed: {res.error}", C["red"])

    btn_repaint = ft.FilledButton("Repaint", icon=ft.Icons.BRUSH_OUTLINED, height=46, on_click=lambda e: page.run_task(do_repaint),
                                   style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF", shape=ft.RoundedRectangleBorder(radius=10), text_style=ft.TextStyle(size=15, weight=ft.FontWeight.BOLD)))
    repaint_body = ft.Column([
        rp_dest, rp_empty,
        ft.Row([rp_song_dd, rp_take_dd], spacing=8),
        ft.Row([ibtn(ft.Icons.PLAY_ARROW_ROUNDED, "Play / pause", rp_toggle, 18),
                ft.OutlinedButton("Play selection", height=34, on_click=rp_play_selection),
                ibtn(ft.Icons.ZOOM_OUT_ROUNDED, "Zoom out", lambda e: rp_apply_zoom(RP["zoom"] / 2), 18),
                ibtn(ft.Icons.ZOOM_IN_ROUNDED, "Zoom in", lambda e: rp_apply_zoom(RP["zoom"] * 2), 18),
                rp_range], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        rp_wave_gd, rp_wave_msg,
        ft.Row([rp_start, rp_end], spacing=8),
        rp_caption, btn_repaint], spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)
    repaint_page = ft.Container(panel(repaint_body, padding=12, expand=True), left=0, top=0, right=0, bottom=0, visible=False)
    ms_hooks.append(refresh_repaint_sources)

    def ct_current():
        if CT["active"] == "lyrics":
            return None
        return next((t for t in CT["tabs"] if t["id"] == CT["active"]), None)

    def ct_label(tab) -> str:
        title = rp_song_title(tab["song_id"])
        lab = "Take"
        for i, _n, takes in rp_audio_library():
            if i == tab["song_id"]:
                lab = next((lb for tid, lb, _p in takes if tid == tab["take_id"]), lab)
                break
        return f"{title} · {lab}"

    def ct_store():
        """Remember the open Repaint tab before another center tab is shown."""
        tab = ct_current()
        if tab is None or not RP["take_id"]:
            return
        rp_save_draft()
        tab["song_id"], tab["take_id"], tab["token"] = RP["song_id"], RP["take_id"], RP["token"]
        tab["snap"] = {"song_id": RP["song_id"], "take_id": RP["take_id"], "path": RP["path"], "token": RP["token"],
                       "wave": RP["wave"], "duration": RP["duration"], "view_start": RP["view_start"], "view_end": RP["view_end"],
                       "sel_start": RP["sel_start"], "sel_end": RP["sel_end"], "zoom": RP["zoom"],
                       "caption": rp_caption.value or "", "msg": rp_wave_msg.value or "", "msg_on": bool(rp_wave_msg.visible)}

    def ct_restore(tab):
        snap = tab.get("snap")
        if not snap:
            select_repaint_source(tab["song_id"], tab["take_id"], load_draft=True)
            return
        RP["song_id"], RP["take_id"], RP["path"] = snap["song_id"], snap["take_id"], snap["path"]
        RP["token"], RP["wave"], RP["duration"] = snap["token"], snap["wave"], snap["duration"]
        RP["view_start"], RP["view_end"] = snap["view_start"], snap["view_end"]
        RP["sel_start"], RP["sel_end"], RP["zoom"] = snap["sel_start"], snap["sel_end"], snap["zoom"]
        rp_caption.value = snap.get("caption") or ""
        rp_wave_msg.value, rp_wave_msg.visible = snap.get("msg") or "", bool(snap.get("msg_on"))
        takes = next((ts for i, _n, ts in rp_audio_library() if i == RP["song_id"]), [])
        rp_lock["on"] = True
        rp_song_dd.value = RP["song_id"]
        rp_take_dd.options = [ft.dropdown.Option(tid, lab) for tid, lab, _p in takes]
        rp_take_dd.value = RP["take_id"]
        rp_lock["on"] = False
        rp_write_times()
        safe_update(rp_song_dd, rp_take_dd, rp_caption, rp_wave_msg)
        if RP["wave"] is not None:
            page.run_task(rp_paint, RP["token"])
        else:
            rp_wave_img.visible = False
            rp_place_overlay()
            safe_update(rp_wave_stack, rp_wave_img)

    def ct_render():
        row = CT.get("row")
        if row is None:
            return
        def chip(key: str, label: str, icon, closable: bool, tip: str):
            on = CT["active"] == key
            fg = "#FFFFFF" if on else C["text2"]
            bits = [ft.Icon(icon, size=14, color=fg),
                    ft.Container(ft.Text(label, size=12, weight=ft.FontWeight.W_600, color=fg, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                 width=max(36, min(160, int(len(label) * 7.2) + 8)))]
            if closable:
                bits.append(ft.IconButton(ft.Icons.CLOSE_ROUNDED, icon_size=14, width=22, height=22, padding=0, tooltip="Close Tab",
                                           icon_color=fg, on_click=lambda e, i=key: ct_close(i)))
            return ft.Container(ft.Row(bits, spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                                padding=po(10, 2, 2 if closable else 10, 2), ink=True, tooltip=tip,
                                bgcolor=C["accent"] if on else C["panel2"], border_radius=br(8),
                                on_click=lambda e, i=key: ct_show(i))
        chips = [chip("lyrics", "Lyrics", ft.Icons.EDIT_NOTE_ROUNDED, False, "Lyrics")]
        for t in CT["tabs"]:
            chips.append(chip(t["id"], ct_label(t), ft.Icons.BRUSH_OUTLINED, True, "Repaint"))
        row.controls = chips
        bar = CT.get("bar")                                          # the tab bar only shows when a Repaint tab sits beside Lyrics; alone, a "Lyrics" tab repeats the title below it
        if bar is not None:
            bar.visible = bool(CT["tabs"])
            safe_update(bar)
        safe_update(row)

    def ct_show(key: str):
        if key != "lyrics" and not any(t["id"] == key for t in CT["tabs"]):
            return
        if key == CT["active"]:
            ct_render()
            return
        ct_store()
        CT["active"] = key
        lyrics_on = key == "lyrics"
        if CT["lyrics"] is not None:
            CT["lyrics"].visible = lyrics_on
        if CT["page"] is not None:
            CT["page"].visible = not lyrics_on
        ct_render()
        safe_update(CT["lyrics"], CT["page"])
        if not lyrics_on:
            tab = ct_current()
            if tab is not None:
                ct_restore(tab)

    def ct_open(song_id: str, take_id: str):
        """One tab per source take. Opening it again only brings that tab forward."""
        if not FF.ENABLE_REPAINT:
            return
        if not song_id or not take_id:
            return
        tab = next((t for t in CT["tabs"] if t["song_id"] == song_id and t["take_id"] == take_id), None)
        if tab is None:
            CT["seq"] += 1
            tab = {"id": f"rp{CT['seq']}", "song_id": song_id, "take_id": take_id, "token": 0,
                   "loading": False, "snap": None, "pending_draft": None}
            CT["tabs"].append(tab)
        if CT["active"] == tab["id"] and (tab.get("snap") or tab.get("loading")):
            ct_render()
            return
        if CT["active"] != tab["id"]:
            ct_store()
            CT["active"] = tab["id"]
            if CT["lyrics"] is not None:
                CT["lyrics"].visible = False
            if CT["page"] is not None:
                CT["page"].visible = True
            safe_update(CT["lyrics"], CT["page"])
        ct_render()
        if tab.get("snap"):
            ct_restore(tab)
        else:
            select_repaint_source(song_id, take_id, load_draft=True)

    def ct_close(tab_id: str):
        tab = next((t for t in CT["tabs"] if t["id"] == tab_id), None)
        if tab is None:
            return
        if CT["active"] == tab_id:
            rp_save_draft()
        elif tab.get("snap"):
            s = tab["snap"]
            try:
                RD.put_draft(ST.library_dir(), KJ.PROJECT_ID, s["song_id"], s["take_id"], {
                    "caption": s.get("caption") or "", "start": s.get("sel_start") or 0, "end": s.get("sel_end") or 0,
                    "zoom": s.get("zoom") or 1, "view_start": s.get("view_start") or 0, "view_end": s.get("view_end") or 0})
            except Exception as ex:
                dbg_once("repaint draft", ex)
        CT["tabs"] = [t for t in CT["tabs"] if t["id"] != tab_id]
        if CT["active"] == tab_id:
            CT["active"] = "lyrics"
            if CT["lyrics"] is not None:
                CT["lyrics"].visible = True
            if CT["page"] is not None:
                CT["page"].visible = False
            safe_update(CT["lyrics"], CT["page"])
        ct_render()

    if lyrics_panel in top_row.controls:
        top_row.controls.remove(lyrics_panel)
    lyrics_slot = ft.Container(lyrics_panel, left=0, top=0, right=0, bottom=0, visible=True)
    center_tab_row = ft.Row(spacing=4, scroll=ft.ScrollMode.AUTO, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    CT["row"], CT["lyrics"], CT["page"] = center_tab_row, lyrics_slot, repaint_page
    CT["bar"] = ft.Container(center_tab_row, height=38, padding=po(0, 2, 0, 0), visible=False)
    center_col_ui.controls = [
        CT["bar"],
        ft.Container(ft.Stack([lyrics_slot, repaint_page], expand=True), expand=True)]
    ct_render()

    def _rp_on_side():
        if CT["active"] == "lyrics" or RP.get("wave") is None:
            return
        w = rp_wave_width()
        if abs(w - int(RP.get("width") or 0)) > 8:
            page.run_task(rp_paint, RP["token"])
    side_hooks.append(_rp_on_side)

    cc_page = ft.Container(cc_panel, left=0, top=0, right=0, bottom=0, visible=False)                 # Rhyme & Syllables / Rhythm & Duration (the same panel; the tab picks the view)
    style_box = ft.Container(ft.Stack([tools_page, report_page, an_page, cc_page, gen_page, settings_page, results_page], expand=True), expand=True)
    rp_title = ft.Text("", size=15, weight=ft.FontWeight.BOLD, color=C["text1"], expand=True)
    rp_back_btn = ibtn(ft.Icons.ARROW_BACK_ROUNDED, "Back", lambda e: rp_back(), 18)
    rp_back_btn.visible = False
    rp_tabs_row = ft.Row(spacing=4, run_spacing=4, wrap=True, expand=True)

    def rp_refresh_header():
        detail = rp["tab"] == "gen" and rp["page"] == "style"
        rp_title.value = RP_MODES[style_tab["v"]][0] if detail else dict(RP_TABS).get(rp["tab"], "")
        rp_title.visible = True
        rp_back_btn.visible = detail
        rp_tabs_row.visible = False
        rp_strip_refresh()
        safe_update(rp_title, rp_back_btn, rp_tabs_row)

    def rp_show_tab(k: str):
        """Lyrics Tools / Analysis / Generate: only one is visible; the others stay built, so nothing is lost when switching."""
        if not (layout["side"] or layout["drawer"]):
            set_sidebar(True)
        if k == "report":                                                      # older callers: the report that is open
            k = "rep_lyrics" if report_view["k"] == "songwriter" else "rep_vocal"
        if k in ("rep_lyrics", "rep_vocal"):
            report_view["k"] = "songwriter" if k == "rep_lyrics" else "music_producer"
        rp["tab"] = k
        tools_page.visible, report_page.visible, an_page.visible, gen_page.visible = k == "tools", k in ("rep_lyrics", "rep_vocal"), k == "an", k == "gen"
        settings_page.visible, results_page.visible = k == "settings", k == "results"
        cc_page.visible = k in ("cc_rhyme", "cc_rhythm")
        safe_update(tools_page, report_page, an_page, cc_page, gen_page, settings_page, results_page)
        rp_refresh_header()
        cc_sync()
        if k in ("rep_lyrics", "rep_vocal"):
            report_pick(report_view["k"])
        elif k == "an":
            render_analysis()
            if an_sub["k"] == "thes":
                refresh_th_preview()
        elif k == "gen":
            rp_build_menu()

    def _rp_slide(detail: bool):
        rp_menu_layer.offset = ft.Offset(-1, 0) if detail else ft.Offset(0, 0)
        rp_detail_layer.offset = ft.Offset(0, 0) if detail else ft.Offset(1, 0)
        safe_update(rp_menu_layer, rp_detail_layer)

    def rp_open_page(tab: str):
        """Generate menu -> page (basic / advanced / ai). Taps during the slide, or when a page is already open, are ignored, so pages never stack. Choosing Basic / Advanced is the same
        choice the old tab buttons made (the song's caption source); nothing is generated or sent here."""
        now = time.monotonic()
        if rp["page"] != "menu" or now < rp["until"]:
            return
        rp["until"] = now + 0.3
        rp["page"] = "style"
        if tab == "ai":
            show_style_tab("ai")
        elif S.song and S.song.settings.style_mode != tab:
            set_style_mode(tab)
        else:
            show_style_tab(tab)
        rp_refresh_header()
        dr_side_buttons()
        _rp_slide(True)

    def rp_back(e=None):
        now = time.monotonic()
        if rp["page"] == "menu" or now < rp["until"]:
            return
        rp["until"] = now + 0.3
        rp["page"] = "menu"
        rp_build_menu()
        rp_refresh_header()
        dr_side_buttons()
        _rp_slide(False)

    side_inner = ft.Column([ft.Row([rp_back_btn, rp_title, rp_tabs_row,
                                    ibtn(ft.Icons.KEYBOARD_DOUBLE_ARROW_RIGHT_ROUNDED, "Hide Style", lambda e: set_sidebar(False))], spacing=2, vertical_alignment=ft.CrossAxisAlignment.START),
                            style_box], spacing=6, expand=True)
    # The right panel has the same frame as the Chat panel and the editor, so the three panels start and end at the same height. The pages inside no longer draw a second frame of their own.
    side_expanded = panel(side_inner, padding=12, expand=True)
    for page_ in (gen_page, settings_page, tools_page, an_page):
        inner_ = page_.content
        if isinstance(inner_, ft.Container):
            inner_.bgcolor, inner_.border, inner_.padding = "#00000000", None, 0
    for inner_ in (review_box, output_panel):
        inner_.bgcolor, inner_.border, inner_.padding = "#00000000", None, 0
    RP_ICONS = {"settings": (ft.Icons.TUNE_ROUNDED, "Song Settings"), "tools": (ft.Icons.EDIT_NOTE_ROUNDED, "Lyrics Tools"), "rep_lyrics": (ft.Icons.ARTICLE_OUTLINED, "Lyric Analysis: the Songwriter AI reads the lyrics and writes its report, with the rhyme report"),
                "rep_vocal": (ft.Icons.RECORD_VOICE_OVER_OUTLINED, "Vocal Analysis: the Music Producer AI reads the song and writes its report on the structure, cues, hook, arrangement and delivery"),
                "cc_rhyme": (ft.Icons.FORMAT_COLOR_TEXT_ROUNDED, "Rhyme & Syllables: the lyrics with the rhyme colours (the Python rhyme analysis: sound rules on the spelling) and the syllable counts (counted by the program)"),
                "cc_rhythm": (ft.Icons.TIMELAPSE_ROUNDED, "Rhythm & Duration: how dense the lines and sections are for the BPM and the length (an estimate counted by the program, no AI)"),
                "gen": (ft.Icons.AUTO_AWESOME_ROUNDED, "Generate"), "results": (ft.Icons.LIBRARY_MUSIC_ROUNDED, "Results")}
    strip_btns = {}

    def rp_strip_open(k: str):
        """An icon of the strip: its panel opens as a drawer over the editor; the same icon again closes it."""
        if (layout["side"] or layout["drawer"]) and rp["tab"] == k:
            set_sidebar(False)
            return
        set_sidebar(True)
        rp_show_tab(k)

    for k_, (ic_, tip_) in RP_ICONS.items():
        strip_btns[k_] = ibtn(ic_, tip_, lambda e, k=k_: rp_strip_open(k), 22, w=38)

    _strip_on: dict = {}

    def rp_strip_refresh():
        changed = []
        for k_, b_ in strip_btns.items():
            on = bool((layout["side"] or layout["drawer"]) and rp["tab"] == k_)
            if _strip_on.get(k_) is on:
                continue
            _strip_on[k_] = on
            b_.icon_color, b_.bgcolor = (C["accent"] if on else C["text2"]), (C["accent_soft"] if on else None)
            changed.append(b_)
        if changed:
            safe_update(*changed)
    side_strip = panel(ft.Column(list(strip_btns.values()), spacing=6, horizontal_alignment=ft.CrossAxisAlignment.CENTER), padding=po(4, 8, 4, 8), width=STRIP_W)
    sidebar_col = ft.Row([side_expanded, side_strip], spacing=4, vertical_alignment=ft.CrossAxisAlignment.STRETCH, expand=True)
    sidebar_holder = ft.Container(content=sidebar_col, width=S.side_w)
    # When the window is too narrow to dock the sidebar, the same Style / Output panel slides over the editor as a drawer (side_strip stays in the layout).
    drawer_layer = ft.Container(visible=False, right=STRIP_W + 4, top=0, bottom=0, width=S.side_w, bgcolor=C["bg"], border_radius=12,
                                shadow=ft.BoxShadow(blur_radius=24, color="#99000000"), padding=po(8, 0, 0, 0))

    def page_w() -> float:
        try:
            return float(page.width or page.window.width or 1500)
        except Exception:
            return 1500.0

    def dlg_h(h: int, frac: float = 0.8) -> int:
        """The height of a dialog's content: the wanted height, but never more than `frac` of the window (a small window gets a scrolling dialog, not one that runs off the screen)."""
        return int(max(240, min(h, page_h() * frac)))

    def page_h() -> float:
        try:
            return float(page.height or page.window.height or 900)
        except Exception:
            return 900.0

    drawer_state = {"open": False}                              # the drawer is never remembered: a narrow window starts with it closed
    layout = {"side": True, "tools": True, "drawer": False}     # what is really shown now (the user's choices live in S.side_open / S.tools_user)

    _aw = {"mode": None}

    def apply_widths(drag: bool = False):
        """Columns keep their minimum widths: the lyrics editor is served first. When the window is too narrow the Style sidebar collapses by itself, then the tools;
        those automatic collapses are NOT the user's choice (S.side_open / S.tools_user stay as they were), so the layout comes back when the window is wide again."""
        avail = page_w() - 20
        ai = max(240.0, min(360.0, S.ai_w))
        side_w = max(320.0, min(560.0, S.side_w))

        def need(side: bool, tools: bool) -> float:
            return ai + 8 + MIN_EDITOR_W + ((TOOLS_W + 8) if tools else 0) + ((side_w + 8 + STRIP_W + 4) if side else STRIP_W + 4)
        side, tools = S.side_open, False                                   # the Lyrics Tools are a Workspace tab now: no column
        can_dock = need(True, tools) <= avail                              # an icon of the strip opens its panel beside the editor; only a window too narrow for that shows it as a drawer
        if side and not can_dock:
            side = False
        if tools and need(side, tools) > avail:
            tools = False
        drawer = (not can_dock) and drawer_state["open"]
        if can_dock:
            drawer_state["open"] = False
        layout["side"], layout["tools"], layout["drawer"] = side, tools, drawer
        ai_holder.width = ai
        same_mode = _aw["mode"] == (side, drawer)                  # a drag that only changes widths leaves the structure alone
        _aw["mode"] = (side, drawer)
        # the Style / Output panel lives in the docked column, or in the drawer layer when it does not fit
        if not (drag and same_mode):
            if drawer:
                sidebar_col.controls = [side_strip]
                drawer_layer.content = side_expanded
            else:
                drawer_layer.content = None
                sidebar_col.controls = [side_expanded, side_strip]
        drawer_layer.visible = drawer
        drawer_layer.width = min(side_w, max(240.0, avail - 40))
        side_expanded.visible, side_strip.visible = (side or drawer), True          # the icon strip is always there
        side_expanded.width = side_w if side else None
        side_div.visible = side
        sidebar_holder.width = (side_w + STRIP_W + 4) if side else STRIP_W
        fit_workspace_height()
        for h in side_hooks:
            h()
        if drag and same_mode:
            safe_update(ai_holder, sidebar_holder, side_expanded, drawer_layer)          # only the boxes whose size changed, not the whole editor page
        else:
            safe_update(editor_page)

    _drag = {"pending": False}

    def _drag_request():
        """A drag event only moves the stored width (the latest wins); the layout is applied at most once per frame, so a fast drag never builds a queue of updates."""
        if _drag["pending"]:
            return
        _drag["pending"] = True

        async def flush():
            await asyncio.sleep(0.016)
            _drag["pending"] = False
            t0 = time.perf_counter()
            apply_widths(drag=True)
            _perf_write("drag_flush", (time.perf_counter() - t0) * 1000)
        page.run_task(flush)

    def _drag_end(e=None):
        _drag["pending"] = False
        apply_widths()
        persist_layout()                                           # saved once, when the divider is let go

    def set_sidebar(open_: bool, focus: str | None = None):
        """focus "style" / "output": the part the user asked for gets most of the sidebar's height. Only the share of the two existing panels changes; nothing is rebuilt,
        so the settings, the output list, its scroll position and the generation status stay as they are."""
        avail = page_w() - 20
        tools = False
        can_dock = (max(240.0, min(360.0, S.ai_w)) + 8 + MIN_EDITOR_W + (max(320.0, min(560.0, S.side_w)) + 8 + STRIP_W + 4)) <= avail
        if can_dock:
            S.side_open = open_
        else:
            drawer_state["open"] = open_
        apply_widths(); persist_layout()
        rp_strip_refresh()
        cc_sync()                                                  # the analysis follows the panel: on while its page is open, off when the panel is closed
        if open_ and focus == "style":
            rp_show_tab("gen")
        elif open_ and focus == "output":
            rp_show_tab("results")

    def on_ai_drag(e):
        S.ai_w = max(240.0, min(360.0, S.ai_w + e.local_delta.x))
        _perf_write("drag_event", 0.0)
        _drag_request()

    def on_side_drag(e):
        S.side_w = max(320.0, min(560.0, S.side_w - e.local_delta.x))
        _perf_write("drag_event", 0.0)
        _drag_request()

    side_div = divider_v(on_side_drag, _drag_end)
    editor_page = ft.Container(padding=10, expand=True, content=ft.Stack([
        ft.Container(left=0, top=0, right=0, bottom=0, content=ft.Row([ai_holder, divider_v(on_ai_drag, _drag_end), ft.Container(center_col_ui, expand=True), side_div, sidebar_holder],
                     spacing=0, vertical_alignment=ft.CrossAxisAlignment.STRETCH)), drawer_layer], expand=True))
    refresh_ws()

    library_page = ft.Container(expand=True, padding=po(16, 14, 16, 10), content=ft.Column([
        ft.Row([ft.Column([ft.Text("My Music", size=22, weight=ft.FontWeight.BOLD, color=C["text1"]), ft.Text("Your music library", size=12, color=C["text3"])], spacing=0), ft.Container(expand=True),
                ft.FilledButton("New Song", icon=ft.Icons.ADD_ROUNDED, on_click=new_song, height=42, style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF", shape=ft.RoundedRectangleBorder(radius=10)))],
               vertical_alignment=ft.CrossAxisAlignment.CENTER),
        cards_row,
        ft.Row([ft.Text("Music Library", size=15, weight=ft.FontWeight.BOLD, color=C["text1"]), lib_count, ft.Container(expand=True), lib_search, sort_dd], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ft.Row([ft.Container(projects_list, expand=2),
                ft.Container(expand=3, content=ft.Column([
                    panel(ft.Column([ft.Text("Song List", size=15, weight=ft.FontWeight.BOLD, color=C["text1"]), tracks_title, ft.Container(tracks_list, expand=True)], spacing=6, expand=True), padding=12, expand=3),
                    ft.Container(height=8),
                    panel(ft.Column([ft.Text("Up Next", size=15, weight=ft.FontWeight.BOLD, color=C["text1"]), queue_src_txt, ft.Container(queue_list, expand=True)], spacing=6, expand=True), padding=12, expand=2)],
                    spacing=0, expand=True))],
               spacing=14, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH)], spacing=12, expand=True))

    page.add(ft.Column([top_bar, page_holder, global_player], spacing=0, expand=True))

    def on_resize(e=None):
        apply_widths()
        if S.ws_open and S.ws_tab == "preview":
            pv_build_layout()
        if S.ws_open and S.ws_tab == "tools":
            tools_layout()
    page.on_resize = on_resize

    # keyboard + close -------------------------------------------------------------------------------------------
    def on_key(e: ft.KeyboardEvent):
        k = (e.key or "").upper()
        if e.ctrl and k == "S":
            save_song_now(e)
        elif e.ctrl and e.shift and k == "E" and S.tab == "editor":
            open_section_options()
        elif e.ctrl and (k == "Y" or (e.shift and k == "Z")):
            do_redo()
    page.on_keyboard_event = on_key

    page.window.prevent_close = True

    def on_window(e):
        if e.type == ft.WindowEventType.CLOSE:
            def really():
                persist_layout()
                pv_stop()
                player.shutdown()
                page.run_task(page.window.destroy)
            if S.gen and S.gen["status"] in ("captioning", "queued", "running"):
                confirm("A song is being generated", "If you close now the result will not be saved into your library. Close anyway?", "Close", lambda: guard_all(really), danger=True)
            else:
                guard_all(really)
    page.window.on_event = on_window

    async def autosave_loop():
        while True:
            await asyncio.sleep(15)
            for sid in list(open_tabs):                          # every open tab with unsaved changes, not only the active one
                sess = sessions.get(sid)
                if sess and is_dirty(sid):
                    try:
                        t0 = time.perf_counter()
                        snap = ST.autosave_snapshot(sess.song)         # the text is made here, so it is consistent; only the file write waits for the disk
                        t1 = time.perf_counter()
                        wrote = await asyncio.to_thread(ST.write_autosave_text, *snap)
                        _perf_write("autosave_snapshot", (t1 - t0) * 1000)
                        _perf_write("autosave_write" + ("" if wrote else "_skipped"), (time.perf_counter() - t1) * 1000)
                    except Exception as ex:
                        dbg_once("autosave", ex)

    # start ------------------------------------------------------------------------------------------------------------
    refresh_lib()
    apply_widths()
    clear_editor()
    set_tab("library")
    update_generate_button()
    if PERF:                                                      # MS_PERF=1: time the UI work that has been slow
        rebuild_lib_page, rebuild_song_list, rebuild_tracks, rebuild_output, rebuild_chat = (_perf(f) for f in (rebuild_lib_page, rebuild_song_list, rebuild_tracks, rebuild_output, rebuild_chat))
        set_tab, refresh_lib, render_tabs, apply_widths, attach = (_perf(f) for f in (set_tab, refresh_lib, render_tabs, apply_widths, attach))
        on_lyrics_change, on_lyrics_selection, refresh_context, _apply_notes, set_lyrics = (_perf(f) for f in (on_lyrics_change, on_lyrics_selection, refresh_context, _apply_notes, set_lyrics))
        on_search_change = _perf(on_search_change)
        _on_resize_timed = _perf(on_resize, "on_resize")
        page.on_resize = _on_resize_timed
        _sel_panels = _perf(_sel_panels, "sel_panels")
        cc_rebuild, cc_tick, li_get, cc_refresh, li_analyze = _perf(cc_rebuild, "cc_rebuild"), _perf(cc_tick, "cc_tick"), _perf(li_get, "li_get"), _perf(cc_refresh, "cc_refresh"), _perf(LI.analyze, "li_analyze")      # MS_PERF=1: the colour column / Live Insights drawing and the analysis
        try:
            page.show_dialog = _perf(page.show_dialog, "show_dialog")
        except Exception as ex:
            log.warning("MS_PERF: show_dialog could not be timed: %r", ex)
    page.run_task(check_connection_loop); page.run_task(player_tick); page.run_task(autosave_loop); page.run_task(work_watch)
    _update_check.start_notice(ft, page, APP_NAME, APP_VERSION)


if __name__ == "__main__":
    ft.run(_app_error.guard(ft, main, APP_NAME, APP_VERSION))
