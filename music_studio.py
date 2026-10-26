"""
WorkflowPad Music Studio: a music library and a lyrics editor for ACE-Step 1.5 (through your own ComfyUI).
Two pages: My Music (library + player) and Editor (lyrics, AI chat, settings, takes). The logic lives in music_studio/ (model, store, compiler,
lyrics_tools, comfy_audio, player, llm_chat); this file is the Flet screen only.
"""
import asyncio, copy, json, logging, logging.handlers, os, re, sys, time, types, zlib
from pathlib import Path
import flet as ft
from flet_code_editor import CodeEditor, CodeLanguage, GutterStyle
import app_error as _app_error
import update_check as _update_check

from music_studio import model as M
from music_studio import store as ST
from music_studio import compiler as K
from music_studio import lyrics_tools as T
from music_studio import tag_parser as TP
from music_studio import cursor_context as CC
from music_studio import section_preview as PV
from music_studio.audio_player import MciPlayer
from music_studio.wmp_player import WmpPlayer
from music_studio import cue_library as CL
from music_studio import lyric_analysis as LA
from music_studio import lyric_rhyme as LR
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
from music_studio import comfy_audio as CA
from music_studio import llm_chat as LC
from music_studio import generation as G
from music_studio.player import PlayerService, QItem

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

APP_ID = "music_studio"
APP_NAME = "WorkflowPad - Music Studio"
APP_VERSION = "0.2.0"
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
        self.arrival_at = -1
        self.arrival_pending = False

    def push_text(self, text: str) -> None:
        self.sent = text
        self.arrival_at = idx16(text, len(text))
        self.arrival_pending = True

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

    def event(self, ev: tuple, current: str):
        """(kind, selection) for a selection event; selection = the new Python-index selection to adopt (None: leave S.sel alone)."""
        kind = classify_selection(ev, self.arrival_pending, self.arrival_at, self.echoes)
        if kind == "arrival":
            self.confirmed, self.arrival_pending = self.sent, False
            return kind, None
        if kind == "stale_echo":
            return kind, None
        if kind == "echo":
            self.confirmed, self.arrival_pending = self.sent, False   # a selection applied after the text can only come from a field that has the text
            sel = self.echo_py.get(ev)
            self.echoes.clear(); self.echo_py.clear()
            return kind, sel
        self.echoes.clear(); self.echo_py.clear()                    # the user acted: nothing we sent earlier may be taken for an echo any more
        self.arrival_pending = False
        client = self.client_text(ev[1], current)
        a, b = idx_from16(client, ev[0]), idx_from16(client, ev[1])
        if client != current:                                       # the song's text is newer than the one the client shows: carry the position over
            a, b = map_index(client, current, a), map_index(client, current, b)
        return kind, (a, b)


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
        sort = "new"
        source = {"type": "all", "id": None}    # what the Song List shows: all | favorites | project (id = song id). NOT the queue that is playing
        queue_src = ""                          # name of the source of the queue that is playing now
        ai_w = cfg_num("ai_w", 270, 240, 360)                 # AI Assistant column
        side_w = cfg_num("side_w", 360, 320, 560)             # Collapsible Right Sidebar, expanded
        side_open = bool(cfg.get("side_open", True))          # the USER's choice; the window may collapse it temporarily (see apply_widths)
        ws_open = bool(cfg.get("ws_open", True))
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
            self.an_corr = {}             # the user's manual syllable corrections of THIS song: {line id: LA.Correction} (saved in analysis.json)
    sessions: dict = {}                         # song id -> Sess (one per open Editor Tab; the song id is the tab key)
    open_tabs: list = []                        # song ids in the order the tabs were opened
    COLLECTIONS = {"all": ("All Songs", ft.Icons.LIBRARY_MUSIC_ROUNDED), "favorites": ("Favorites", ft.Icons.STAR_ROUNDED)}

    # ── generic helpers ──────────────────────────────────────────────────────────────────────────────────
    def toast(msg: str, color: str | None = None):
        page.show_dialog(ft.SnackBar(ft.Text(msg, color="#FFFFFF"), bgcolor=color or "#243653", duration=3500))

    _dbg_seen: set = set()

    def dbg_once(where: str, ex: Exception) -> None:
        """A swallowed error goes to the debug log ONCE per (place, error kind, message): timers and repeated redraws can never flood the log, and nothing is shown to the user or raised."""
        key = (where, type(ex).__name__, re.sub(r"\d+", "#", str(ex))[:80])          # control ids / counters in the text must not make every repeat look new
        if key in _dbg_seen or len(_dbg_seen) >= 300:
            return
        _dbg_seen.add(key)
        log.debug("ignored error in %s: %s: %s", where, type(ex).__name__, ex)

    def safe_update(*ctls):
        for c in ctls:
            try:
                c.update()
            except Exception as ex:
                dbg_once("update " + type(c).__name__, ex)

    def ibtn(icon, tip, on_click, size=18, color=None, disabled=False, w=34):
        return ft.IconButton(icon, icon_size=size, tooltip=tip, on_click=on_click, icon_color=color or C["text2"], width=w, height=w, padding=0, disabled=disabled)

    def field_style(**kw):
        d = dict(text_size=12, height=48, border_radius=8, border_color=C["border"], focused_border_color=C["accent"], bgcolor=C["field"], color=C["text1"],
                 label_style=ft.TextStyle(color=C["text3"], size=11), content_padding=po(left=10, right=10, top=6, bottom=6))
        d.update(kw)
        return d

    def dd(label, options, value, on_select, width=None, editable=False):
        d = ft.Dropdown(label=label, value=value, options=[ft.dropdown.Option(k, t) for k, t in options], on_select=on_select, width=width, editable=editable,
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
        favs = [t for t in entry["takes"] if t.favorite]
        return (favs[-1] if favs else entry["latest"])

    def qitem(song: M.Song, t: M.Take) -> QItem:
        return QItem(song.id, t.id, song.title, f"Take {t.number:02d}", str(ST.take_path(t)))

    # ═══ EDITOR: lyrics ═════════════════════════════════════════════════════════════════════════════════════
    title_tf = ft.TextField(value="", hint_text="Song title", text_size=15, border=ft.InputBorder.NONE, color=C["text1"], dense=True, width=230,
                            content_padding=po(0, 4, 0, 4), text_style=ft.TextStyle(weight=ft.FontWeight.BOLD), hint_style=ft.TextStyle(color=C["text3"]))
    # The lyrics field is a flet-code-editor CodeEditor used as a plain-text editor: line numbers, no highlighting, no autocomplete, no folding. It scrolls by itself (there is no scroll
    # API to drive, so nothing can make it jump), keeps its caret when the program sets the text, and does not wrap: a line is as long as the user writes it.
    def make_editor(value: str = "") -> CodeEditor:
        return CodeEditor(value=value, language=CodeLanguage.PLAINTEXT, autocomplete=False, expand=True,
                          code_theme=ft.MarkdownCustomCodeTheme(root=ft.TextStyle(color=C["ink"], bgcolor=C["paper"])),
                          text_style=ft.TextStyle(size=15, height=1.55, color=C["ink"]),
                          gutter_style=GutterStyle(show_line_numbers=True, show_folding_handles=False, show_errors=False, background_color=C["paper"],
                                                   text_style=ft.TextStyle(size=12, color=C["paper_dim"])),
                          padding=po(left=4, right=16, top=12, bottom=12))
    # One editor PER OPEN SHEET, all of them stacked in the page and all built the same way: the active one fills the Stack, the others are parked outside it (clipped, so they cannot be
    # seen or clicked). They are neither hidden (a hidden editor loses its scroll position and shares it with the next one) nor resized or made transparent (changing `expand`, `opacity`
    # or `ignore_interactions` changes the widget tree and rebuilds the control, which loses it too). So every sheet
    # keeps its own scroll position, caret and selection by itself. `lyrics_tf` always names the ACTIVE editor.
    lyrics_tf = make_editor()
    blank_ed = lyrics_tf                                                    # shown when no song is open
    editor_pool: dict = {}                                                  # (song id, sheet id) -> its CodeEditor
    PARK = 20000                                                            # how far an inactive editor is moved sideways (its width stays the same)
    editor_fresh: set = set()                                               # editors made but not given their caret yet
    editor_wrap: dict = {None: ft.Container(lyrics_tf, left=0, top=0, right=0, bottom=0)}     # the same keys -> the Container that places it
    editor_col = ft.Stack([editor_wrap[None]], expand=True)
    editor_holder = ft.Container(expand=True, bgcolor=C["paper"], border_radius=8, content=editor_col, clip_behavior=ft.ClipBehavior.ANTI_ALIAS)
    sheet_tabs = ft.Row(spacing=4, scroll=ft.ScrollMode.AUTO, expand=True, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    sheet_plus_holder = ft.Container()
    sheet_bar = ft.Container(height=38, padding=po(0, 2, 0, 0), content=ft.Row([sheet_tabs, sheet_plus_holder], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER))   # fixed: it never scrolls with the lyrics
    notes_txt = ft.Text("", size=11, color=C["amber"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
    sel_chip = ft.Text("", size=11, color=C["text3"])
    # One-click access to Section Options: shown only while the caret is inside a [tag] (the field's right-click menu cannot be extended in Flet).
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
    btn_undo = ibtn(ft.Icons.UNDO_ROUNDED, "Undo", lambda e: do_undo())
    btn_redo = ibtn(ft.Icons.REDO_ROUNDED, "Redo", lambda e: do_redo())
    dirty_txt = ft.Text("", size=11, color=C["amber"])
    _guard = {"set": False}
    # Programmatic-edit bookkeeping (everything the lyrics field and its scroll column need to tell the user's actions from ours).
    book = SelectionBook()
    _ui = {
        "epoch": 0,                          # +1 when another song is opened: work started for the previous song is void
        "rev": 0,                            # +1 for every programmatic push of the text into the field
        "busy": False, "pending": None,      # one push runs at a time; a newer request replaces one that has not started
        "irev": 0,                           # +1 whenever the USER acts on the editor (clicks / moves the caret, types, scrolls): older restores must give way
        "arrival": None,                     # asyncio.Event a push waits on until the field reports that the pushed text arrived
        "restoring": False, "pushing": False, "sel_evt": None,
        "focused": False, "focus_evt": None, # does the field have focus (from its own on_focus / on_blur); Event while a focus request is waiting to be confirmed
        "notes_due": 0.0, "notes_task": False, "notes_for": None,
    }

    def lyr() -> str:
        return S.song.lyrics if S.song else ""

    dr_hooks: list = []                                  # the AI Music Director re-checks "is my draft still current" whenever the song changes

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

    async def _push_once(req: dict):
        """Text, then selection, then focus. The CodeEditor moves its caret to the END of a text it receives, so the selection is sent only after the field has reported that the
        text arrived (bounded wait); neither the text nor the focus scrolls it by itself. A text the field already shows is not sent again, and a sheet's editor that is merely
        shown again keeps its own caret and scroll (`keep`). While the push runs, the field's selection events are ours. A request for another song / sheet, or one that a newer
        request has replaced, stops before the step it has not done yet."""
        def current() -> bool:
            return bool(S.song) and S.song.id == req["song"] and _ui["epoch"] == req["epoch"]

        try:
            if not current():
                log.debug("push rev=%s skipped: song changed", req["rev"])
                return
            text = lyr()
            _ui["pushing"] = True                               # selection events of the field are ours until the push is over
            changed = (lyrics_tf.value or "") != text
            log.debug("push rev=%s song=%s sheet=%s old_len=%d new_len=%d changed=%s S.sel=%s field.sel=%s editor_focused=%s request_focus=%s keep=%s",
                      req["rev"], S.song.id, S.song.book.active_id, len(lyrics_tf.value or ""), len(text), changed, S.sel,
                      (lyrics_tf.selection.base_offset, lyrics_tf.selection.extent_offset) if lyrics_tf.selection else None, _ui["focused"], req["focus"], req["keep"])
            fresh_key = (S.song.id, S.song.book.active_id)
            fresh = fresh_key in editor_fresh
            editor_fresh.discard(fresh_key)
            if changed:
                book.confirmed = book.sent = text               # what the field will show: the offsets of its next events refer to this text
                book.echoes.clear(); book.echo_py.clear(); book.arrival_pending = False
                arrival = asyncio.Event()
                _ui["arrival"] = arrival
                lyrics_tf.value = text
                if req["sel"]:                                  # text and caret in ONE update: the field never shows its caret at the end of the new text, so it never scrolls there
                    a0, b0 = min(S.sel[0], len(text)), min(S.sel[1], len(text))
                    ev0 = book.push_selection(text, a0, b0)
                    lyrics_tf.selection = ft.TextSelection(base_offset=ev0[0], extent_offset=ev0[1])
                lyrics_tf.update()
                log.debug("push rev=%s: value+selection sent in one update (selection %s)", req["rev"], (lyrics_tf.selection.base_offset, lyrics_tf.selection.extent_offset) if lyrics_tf.selection else None)
                try:                                            # the field puts its caret at the end of a new text; a selection sent before that happened would be undone
                    await asyncio.wait_for(arrival.wait(), 1.5)
                    log.debug("push rev=%s: the field reported the new text; field.sel now %s", req["rev"], (lyrics_tf.selection.base_offset, lyrics_tf.selection.extent_offset) if lyrics_tf.selection else None)
                except asyncio.TimeoutError:
                    log.debug("push rev=%s: the field did not report the new text in time", req["rev"])
            if not current() or _ui["pending"] is not None:
                return
            if req["focus"]:
                await lyrics_tf.focus()
            if req["sel"] and current() and _ui["pending"] is None and (changed or fresh or not req["keep"]):
                a, b = min(S.sel[0], len(text)), min(S.sel[1], len(text))
                ev = book.push_selection(text, a, b)
                log.debug("push rev=%s: second selection update -> %s (editor_focused=%s)", req["rev"], ev, _ui["focused"])
                sel_evt = asyncio.Event()
                _ui["sel_evt"] = sel_evt
                lyrics_tf.selection = ft.TextSelection(base_offset=ev[0], extent_offset=ev[1])
                lyrics_tf.update()
                try:
                    await asyncio.wait_for(sel_evt.wait(), 0.8)
                except asyncio.TimeoutError:
                    log.debug("push rev=%s: no selection report in time", req["rev"])
        except Exception:
            log.exception("push rev=%s failed", req["rev"])
        finally:
            _ui["pushing"] = False; _ui["arrival"] = None; _ui["sel_evt"] = None
            refresh_tag_chip()
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
        typed = lyrics_tf.value or ""
        if typed == S.song.lyrics:
            if _ui["arrival"] is not None:
                _ui["arrival"].set()                            # the field reporting a text we pushed: a push waits for this
            return                                              # nothing was typed
        book.typed(typed)                                       # the user typed it: this is what the field holds now (proof)
        set_lyrics(typed, None, burst=True, programmatic=False)

    _tl_hook = {"fn": None, "tick": None}                                   # the Workspace Timeline redraws when the takes, the song or the player state change (set once the Timeline exists)
    _hooks = {"selection": None}                                # the Thesaurus remembers what the user selected (read-only: it never changes the field or S.sel)

    def on_lyrics_selection(e):
        if _guard["set"] or not S.song or e.control is not lyrics_tf:
            return
        s = lyrics_tf.selection
        if s is None:
            return
        ev = (min(s.start, s.end), max(s.start, s.end))
        if _ui["pushing"]:                                      # the field moving its caret because of the text we sent, or confirming the selection we sent: not the user
            if _ui["sel_evt"] is not None and book.echoes and ev == book.echoes[-1]:
                _ui["sel_evt"].set()
            return
        kind, sel = book.event(ev, lyr())
        log.debug("selection event %s -> %s%s", ev, kind, f" S.sel {sel}" if sel else "")
        if kind == "arrival" or kind == "stale_echo":
            return
        if sel is not None:
            S.sel = sel
        a, b = S.sel
        chip = f"{b - a} chars selected" if b > a else ""
        if chip != sel_chip.value:
            sel_chip.value = chip
            safe_update(sel_chip)
        refresh_chat_ctx()
        refresh_tag_chip()
        refresh_context()                                       # the Lyrics Tools sidebar (only rebuilt when what it shows changes); the Thesaurus hook below is separate and untouched
        if _hooks["selection"] is not None:
            try:
                _hooks["selection"](kind, S.sel)
            except Exception as ex:                             # a Thesaurus problem must never disturb the editor
                log.debug("thesaurus selection hook failed: %r", ex)

    def _on_ed_focus(e):
        if e.control is lyrics_tf:
            _ui["focused"] = True
            log.debug("editor focus (current editor)")

    def _on_ed_blur(e):
        if e.control is lyrics_tf:
            _ui["focused"] = False
            log.debug("editor blur (current editor)")

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
            editor_col.controls.remove(editor_wrap.pop(k))
            del editor_pool[k]
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
        log.debug("use_editor key=%s pool=%s", key, list(editor_pool))
        safe_update(editor_col)

    wire_editor(lyrics_tf)

    def _restore(step):
        text, sel = step
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
                "Cursor anywhere else, or text selected: adds a [{p}] line at that spot (experimental).\n"
                "Note: this is a hint to the model. ACE-Step may not follow it every time.")
    _TIP_INLINE = ("Puts [{p}] exactly where the cursor is, on the same line (no new line is added, like Hold Note). If text is selected it goes before the selection.\n"
                   "Cursor on a section line: [Chorus] → [Chorus - {p}] (click again to remove it).\n"
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
                ("Chorus", "Chorus", ft.Icons.STAR_ROUNDED, "Chorus\nThe hook of the song: the part with the most memorable melody or lyrics, often repeated.\n" + _TIP_SECTION.format(n="Chorus"))]),
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
                ("soft", "Soft Vocals", ft.Icons.VOLUME_MUTE_ROUNDED, "Soft Vocals\nAsks for a soft, gentle voice.\n" + _TIP_INLINE.format(p="soft vocals")),
                ("raspy", "Raspy / Gritty", ft.Icons.GRAIN_ROUNDED, "Raspy / Gritty Vocals\nAsks for a rough, raspy voice.\n" + _TIP_INLINE.format(p="raspy vocals")),
                ("breathy", "Breathy Vocals", ft.Icons.AIR_ROUNDED, "Breathy Vocals\nAsks for an airy, breathy voice.\n" + _TIP_INLINE.format(p="breathy vocals")),
                ("choir", "Choir / Backing", ft.Icons.GROUPS_ROUNDED, "Choir / Backing Vocals\nAsks for a choir or group of backing voices.\n" + _TIP_INLINE.format(p="choir vocals")),
                ("emotional", "Emotional Vocals", ft.Icons.FAVORITE_BORDER_ROUNDED, "Emotional Vocals\nAsks for a voice full of feeling.\n" + _TIP_INLINE.format(p="emotional vocals")),
                ("operatic", "Operatic Vocals", ft.Icons.THEATER_COMEDY_ROUNDED, "Operatic Vocals\nAsks for an opera-style voice.\n" + _TIP_INLINE.format(p="operatic vocals")),
                ("hold", "Hold Note", ft.Icons.TIMELAPSE_ROUNDED, "Hold Note\nStretches a sung word so it is held longer.\nSelect the word first, or put the cursor right after it.\nThe program lengthens the end of the word and adds [Hold] at the start of the line.\nExample: love → [Hold] loveeee\nNote: experimental. It can distort words (Thai especially), and the AI is not guaranteed to hold the note.")])]),
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

    copy_menu = ft.PopupMenuButton(icon=ft.Icons.CONTENT_COPY_ROUNDED, icon_size=18, icon_color=C["text2"], tooltip="Copy / export lyrics",
                                   items=[ft.PopupMenuItem(content=ft.Text("Copy original lyrics"), on_click=lambda e: page.run_task(copy_variant, "original")),
                                          ft.PopupMenuItem(content=ft.Text("Copy ACE-Step lyrics"), on_click=lambda e: page.run_task(copy_variant, "ace")),
                                          ft.PopupMenuItem(content=ft.Text("Copy for AI (with notes)"), on_click=lambda e: page.run_task(copy_variant, "ai")),
                                          ft.PopupMenuItem(content=ft.Text("Export TXT (ACE-Step)"), on_click=export_txt)])

    # ═══ EDITOR: Section Options (cue editor for one [tag]) ═════════════════════════════════════════════════
    # The same cue editor (make_section_editor) serves the Lyrics Tools sidebar (it follows the caret) and the Section Options dialog (button / Ctrl+Shift+E). A right-click cannot be used
    # to choose the tag: Flet's TextField reports no caret move and no character offset for it (see test_context_menu_proto.py), so the tag is chosen with the caret.
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

    def apply_section_target(tg: dict, new_tag: str | None, quiet: bool = False) -> bool:
        """Apply (new_tag) or remove (None) the tag of `tg`. The target is checked against what is shown NOW: same song, same sheet, same editor epoch, and the lyrics still hold that exact
        tag at that exact place. Anything else means nothing is applied and the user is told (the draft is never moved to the caret's new place). One undo step through set_lyrics."""
        if not (S.song and S.song.id == tg["song"] and S.song.book.active_id == tg["sheet"] and _ui["epoch"] == tg["epoch"]):
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
        set_lyrics(new_text, (caret, caret), refocus=False)          # the normal editing path: one undo step, caret kept; no forced focus (it scrolled the editor)
        if not quiet:
            toast("Tag removed" if new_tag is None else f"Updated {new_tag}", C["green"])
        return True

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
        hooks = {"auto": None}

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
            safe_update(err_txt, warn_txt, apply_btn)

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
                                bgcolor=C["accent"] if on0 else C["panel2"], border=ft.Border.all(1, C["accent"] if on0 else C["border"]), tooltip=f"adds “{c.phrase}” to the tag")

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
                cue_area.controls = [s_tf] + (grouped([c for c in CL.CUES if not q or q in c.label.lower()]) or [ft.Text("No cue matches.", size=12, color=C["text3"])])
            else:
                note = []
                if original:
                    note = [ft.Text(f"The existing cue text could not be split into known cues, so it is kept exactly as written: “{original}”. Edit it here if you want to change it.", size=11, color=C["text3"])]
                cue_area.controls = note + [ft.TextField(label="Custom Cue", value=st["custom"], hint_text="for example: soft bells", **field_style(height=48), on_change=on_custom)]
            safe_update(tabs_row, cue_area)

        def on_search(ev):
            st["search"] = ev.control.value or ""
            q = st["search"].strip().lower()
            cue_area.controls = [cue_area.controls[0]] + (grouped([c for c in CL.CUES if not q or q in c.label.lower()]) or [ft.Text("No cue matches.", size=12, color=C["text3"])])
            safe_update(cue_area)

        def on_custom(ev):
            st["custom"] = ev.control.value or ""
            refresh_preview()

        def pick_tab(k: str):
            st["tab"] = k
            build_area()

        def start():
            if not standalone:
                build_area()
            validate(preview_tf.value or "")

        def dirty() -> bool:
            return (preview_tf.value or "").strip() != tg["ref"].original

        return types.SimpleNamespace(tg=tg, hooks=hooks, standalone=standalone, preview_tf=preview_tf, edit_tf=edit_tf, err_txt=err_txt, warn_txt=warn_txt, cue_area=cue_area, tabs_row=tabs_row,
                                     apply_btn=apply_btn, start=start, dirty=dirty)

    def show_section_dialog(tg: dict):
        ref, parsed = tg["ref"], tg["parsed"]
        ed = make_section_editor(tg, compact=False, cue_w=165, area_h=250)

        def close(ev=None):
            page.pop_dialog()                                    # Cancel / close: nothing was changed

        async def finish_async(new_tag: str | None):
            """Apply first, close the dialog when the editor has received the new text AND its selection. When the dialog closes, Flutter hands the focus back to the editor; if that happened in the
            middle of the push the editor would be focused while it holds a half-applied state and could scroll to it. (Waits for the push to finish: no timer is used to force a scroll.)"""
            applied = apply_section_target(tg, new_tag)          # checks song, sheet, epoch and the exact tag again
            if applied:
                for _ in range(120):                              # at most ~6 s; the push normally takes about a second
                    if not _ui["busy"] and _ui["pending"] is None:
                        break
                    await asyncio.sleep(0.05)
            page.pop_dialog()

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
                          ed.tabs_row, ed.cue_area, ed.edit_tf,
                          ft.Divider(height=8, color=C["border"]),
                          ft.Text("CURRENT", size=10, weight=ft.FontWeight.BOLD, color=C["text3"]), ft.Text(ref.original, size=13, color=C["text1"], selectable=True),
                          ed.preview_tf, ed.warn_txt, ed.err_txt], spacing=6, tight=True, scroll=ft.ScrollMode.AUTO)
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Row([ft.Text("Section Options"), ft.Container(expand=True), ibtn(ft.Icons.CLOSE_ROUNDED, "Close", close)]),
                                        content=ft.Container(body, width=560, height=520), actions=actions))
        ed.start()

    # ═══ EDITOR: the Lyrics Tools sidebar follows the caret (cursor_context.py): a section tag -> its cue editor, sung lyrics / a selection / an empty line -> the tools that fit, any other tag
    # -> a small tag editor. The sidebar is only rebuilt when what it shows changes (the context signature), never on every key; the old toolbox stays under "All Tools". ═════════════
    TOOL_INFO = {key: (label, icon, tip, g[4]) for g in TOOL_GROUPS for _t, tools in g[5] for key, label, icon, tip in tools}
    CTX_VOCAL = ["whisper", "soft", "breathy", "belting", "falsetto", "spoken", "raspy", "emotional"]
    CTX_BACKING = ["harmonies", "adlib", "hold", "build"]
    CTX_SECTIONS = ["Verse", "Pre-Chorus", "Chorus", "Bridge", "Intro", "Outro"]

    def ctx_tools(title: str, keys: list):
        return ft.Column([ft.Text(title, size=10, weight=ft.FontWeight.BOLD, color=C["text3"]), *[tool_btn(TOOL_INFO[k][0], TOOL_INFO[k][1], k, TOOL_INFO[k][3], TOOL_INFO[k][2]) for k in keys]], spacing=4)

    ctx_hold_hint = ft.Text("Hold Note: select the word first (or put the cursor right after it).", size=10, color=C["text3"])
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
        ed.apply_btn.on_click = lambda ev: do_apply(ed.preview_tf.value or "")
        rm = ft.TextButton("Remove tag", on_click=lambda ev: do_apply(None), style=ft.ButtonStyle(color=C["red"], padding=po(4, 0, 4, 0)))
        if ed.standalone:
            body = [ed.edit_tf, ed.preview_tf, ed.warn_txt, ed.err_txt, ft.Row([ed.apply_btn, rm], spacing=4, wrap=True)]
        else:
            body = [ed.tabs_row, ed.preview_tf, ed.warn_txt, ed.err_txt, ft.Row([ed.apply_btn, rm], spacing=4, wrap=True), ed.cue_area]      # the preview and Apply stay in view; the cue list below can be long
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
    search_tf = ft.TextField(hint_text="Search songs...", prefix_icon=ft.Icons.SEARCH_ROUNDED, on_change=lambda e: (setattr(S, "search", e.control.value or ""), rebuild_song_list(), rebuild_lib_page()),
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
        _ui.update(epoch=_ui["epoch"] + 1, arrival=None, pending=None, irev=_ui["irev"] + 1, focused=False, notes_for=None)
        book.reset()
        use_editor()
        _guard["set"] = True
        title_tf.value = song.title
        _guard["set"] = False
        dirty_txt.value = "● unsaved" if sess.dirty else ""
        a, b = sess.sel
        sel_chip.value = f"{b - a} chars selected" if b > a else ""
        chat_busy.visible = S.chat_busy_song == song.id
        show_lyrics_to_ui(select=sess.sel, keep_view=True)
        build_settings(); rebuild_output(); rebuild_chat(); rebuild_song_list(); sync_undo_btns(); update_generate_button()
        safe_update(title_tf, dirty_txt, sel_chip, chat_busy)
        refresh_tag_chip(); refresh_context(force=True)
        render_analysis()
        render_tabs()
        render_sheet_bar()

    def load_song_into_editor(song: M.Song, dirty: bool = False):
        """A song that is not open yet (or whose recovered autosave replaces its session): new session, new tab if needed."""
        park()
        sess = Sess(song, dirty)
        sessions[song.id] = sess
        load_sess_analysis(sess)
        if song.id not in open_tabs:
            open_tabs.append(song.id)
        attach(sess, park_first=False)

    def activate(song_id: str):
        sess = sessions.get(song_id)
        if not sess:
            return
        if S.song and S.song.id == song_id and S.tab == "editor":
            return
        attach(sess)
        set_tab("editor")

    def open_song(song_id: str, then_tab: str | None = None):
        """Edit Song: one tab per song id; an open song is only activated, so nothing unsaved is touched."""
        if song_id in sessions:
            activate(song_id)
            return
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
                                             "thes": sess.thes, "thes_sel": sess.thes_sel, "an_corr": sess.an_corr}

    def sheet_rt_load(sess):
        r = sess.rt.pop(sess.song.book.active_id, None)
        if r is None:                                           # a sheet seen for the first time (or after a restart): empty history, caret at the start, its own analysis from disk
            sess.hist, sess.sel, sess.scroll = EditHistory(), (0, 0), 0.0
            sess.analysis = sess.rhyme = sess.rhythm = sess.thes = sess.thes_sel = None
            sess.an_corr = {}
            load_sess_analysis(sess)
        else:
            sess.hist, sess.sel, sess.scroll = r["hist"], r["sel"], r["scroll"]
            sess.analysis, sess.rhyme, sess.rhythm, sess.thes, sess.thes_sel, sess.an_corr = r["analysis"], r["rhyme"], r["rhythm"], r["thes"], r["thes_sel"], r["an_corr"]
        S.hist, S.sel = sess.hist, sess.sel

    def refresh_editor_for_sheet(sess):
        _ui.update(epoch=_ui["epoch"] + 1, arrival=None, pending=None, irev=_ui["irev"] + 1, focused=False, notes_for=None)     # work started for the sheet that was left is void
        book.reset()
        use_editor()
        a, b = S.sel
        sel_chip.value = f"{b - a} chars selected" if b > a else ""
        show_lyrics_to_ui(select=S.sel, keep_view=True)
        build_settings(); sync_undo_btns(); update_generate_button()
        safe_update(sel_chip)
        refresh_tag_chip(); refresh_context(force=True); refresh_chat_ctx(); render_analysis()

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
            sheet_tabs.controls = []
            sheet_plus_holder.content = None
            safe_update(sheet_tabs, sheet_plus_holder)
            return
        sess = sessions.get(S.song.id)
        b = S.song.book
        tabs = []
        for sid in b.open_ids:
            sh = SH.sheet_of(b, sid)
            if sh is None:
                continue
            on = sid == b.active_id
            fg = "#FFFFFF" if on else C["text2"]
            row = [ft.Container(ft.Text(sh.name, size=12, weight=ft.FontWeight.W_600, color=fg, max_lines=1, overflow=ft.TextOverflow.ELLIPSIS), width=max(36, min(130, int(len(sh.name) * 7.5) + 4)))]
            if sess is not None and sid in sess.dirty_sheets:
                row.append(ft.Text("●", size=10, color=C["amber"], tooltip="Unsaved changes in this sheet"))
            row.append(ft.PopupMenuButton(icon=ft.Icons.MORE_VERT_ROUNDED, icon_size=14, icon_color=fg, tooltip="Sheet options", items=[
                ft.PopupMenuItem(content=ft.Text("Rename"), on_click=lambda e, i=sid: sheet_rename_dialog(i)),
                ft.PopupMenuItem(content=ft.Text("Duplicate"), on_click=lambda e, i=sid: sheet_duplicate(i)),
                ft.PopupMenuItem(content=ft.Text("Close tab (keeps the sheet)"), on_click=lambda e, i=sid: sheet_close(i)),
                ft.PopupMenuItem(content=ft.Text("Delete…"), on_click=lambda e, i=sid: sheet_delete_confirm(i))]))
            tabs.append(ft.Container(ft.Row(row, spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER), padding=po(10, 2, 0, 2), ink=True, tooltip=sh.name,
                                     bgcolor=C["accent"] if on else C["panel2"], border_radius=br(8), on_click=lambda e, i=sid: sheet_switch(i)))
        items = [ft.PopupMenuItem(content=ft.Text("New blank sheet"), on_click=sheet_new),
                 ft.PopupMenuItem(content=ft.Text("Duplicate this sheet"), on_click=lambda e: sheet_duplicate(b.active_id))]
        for h in SH.hidden(S.song):
            items.append(ft.PopupMenuItem(content=ft.Text(f"Open “{h.name}”"), on_click=lambda e, i=h.id: sheet_switch(i)))
        sheet_tabs.controls = tabs
        sheet_plus_holder.content = ft.PopupMenuButton(icon=ft.Icons.ADD_ROUNDED, icon_size=18, icon_color=C["accent"], tooltip="New sheet / reopen a closed sheet", items=items)
        safe_update(sheet_tabs, sheet_plus_holder)

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
                            on_click=lambda e: set_tab("library"))]
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
        _ui.update(epoch=_ui["epoch"] + 1, arrival=None, pending=None, irev=_ui["irev"] + 1)
        book.reset()
        _guard["set"] = False
        S.chat, S.takes, S.runs = [], [], []
        dirty_txt.value = ""; sel_chip.value = ""
        rebuild_output(); rebuild_chat(); build_settings(); update_generate_button()
        safe_update(title_tf, lyrics_tf, dirty_txt, sel_chip)
        refresh_tag_chip(); refresh_context(force=True)
        render_analysis()

    # ═══ EDITOR: style / options ════════════════════════════════════════════════════════════════════════════
    settings_col = ft.Column(spacing=8, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
    adv_tile = ft.ExpansionTile(title=ft.Text("Advanced Options", size=12, color=C["text2"]), controls=[], tile_padding=po(left=2, right=2), controls_padding=po(left=2, right=2, bottom=6),
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
        if S.song and S.song.settings.workflow not in names:
            S.song.settings.workflow = names[0] if names else ""
        return names

    # style mode (Basic / Advanced) + prompt history
    prompt_tf = ft.TextField(label="Music Style Prompt", multiline=True, min_lines=5, max_lines=9,
                             hint_text="Describe the music style, for example: A cinematic alternative rock ballad with emotional male vocals, atmospheric guitars, powerful drums and a dramatic final chorus.",
                             **field_style(height=None, text_size=13, hint_style=ft.TextStyle(color=C["text3"], size=12)))
    hist_title = ft.Text("Prompt History", size=12, color=C["text2"])
    history_tile = ft.ExpansionTile(title=hist_title, controls=[], tile_padding=po(left=2, right=2), controls_padding=po(left=2, right=2, bottom=6), maintain_state=True,
                                    expanded_cross_axis_alignment=ft.CrossAxisAlignment.START, text_color=C["text2"])
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
    dr = {"snap": None, "prop": None, "sel": 0, "fields": set(), "busy": False, "error": None, "undo": None}
    dr_stale_txt = ft.Text("", size=11, color=C["amber"], visible=False, weight=ft.FontWeight.W_600)

    def dr_ask_fn(backend: str, host: str):
        def ask(system, user):
            if backend == "grok":
                g = LC.grok_creds()
                return LC.ask_grok(g["key"], g["model"], system, user)
            return LC.ask_local(host, LLM_DIR / backend.split(":", 1)[1], system, user)
        return ask

    def dr_current():
        return S.song.id, S.song.book.active_id, lyr(), SH.settings_to_dict(S.song.settings)

    def dr_stale() -> str:
        if dr["snap"] is None:
            return ""
        if not S.song:
            return "No song is open — Reanalyze."
        sid, sh, ly, st = dr_current()
        return AD.stale_reason(dr["snap"], sid, sh, ly, st)

    def dr_tick():
        """Called whenever the song changes: the 'Lyrics changed — Reanalyze' line appears (and Apply is disabled) as soon as the draft is out of date."""
        if dr["prop"] is None or style_tab["v"] != "ai":
            return
        why = dr_stale()
        if why != dr_stale_txt.value:
            dr_stale_txt.value, dr_stale_txt.visible = why, bool(why)
            dr_apply_btn.disabled = bool(why) or dr["busy"]
            safe_update(dr_stale_txt, dr_apply_btn)
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
        snap = AD.make_snapshot(sid, sh, SH.active_sheet(S.song).name, S.song.title, ly, st)
        page.run_task(dr_run, snap, S.backend, S.host)

    async def dr_run(snap, backend: str, host: str):
        loop = asyncio.get_running_loop()
        dr.update(busy=True, error=None)
        S.chat_busy_song = snap.song_id
        dr_render()
        prop, err = None, None
        try:
            prop = await loop.run_in_executor(None, lambda: AD.analyze(dr_ask_fn(backend, host), snap))     # one question, no automatic retry
        except AD.DirectorError as ex:
            err = ex
            log.debug("AI Music Director refused the answer: %s\nRAW:\n%s", ex.problems, ex.raw)
        except Exception as ex:
            err = AD.DirectorError(f"AI error: {ex}")
        finally:
            S.chat_busy_song = ""
        dr["busy"] = False
        if err is not None:
            dr["error"] = err                                   # the previous draft (if any) stays; the failed raw answer is kept for the debug view
        else:
            dr.update(snap=snap, prop=prop, sel=0, error=None, fields=AD.default_selection(snap, prop, prop.directions[0]))
        dr_render()

    def dr_pick(i: int):
        """Clicking a direction only previews it: no SongSettings field changes."""
        if dr["prop"] is None:
            return
        dr["sel"] = i
        dr["fields"] = AD.default_selection(dr["snap"], dr["prop"], dr["prop"].directions[i])
        dr_render()

    def dr_toggle(name: str):
        dr["fields"] = dr["fields"] ^ {name}
        dr_render()

    def dr_rebase(song_id, sheet_id, lyrics):
        dr["snap"] = AD.make_snapshot(song_id, sheet_id, SH.active_sheet(S.song).name, S.song.title, lyrics, SH.settings_to_dict(S.song.settings))

    def dr_apply(e=None):
        if dr["prop"] is None or not S.song:
            return
        d = dr["prop"].directions[dr["sel"]]
        sid, sh, ly, st = dr_current()
        try:
            plan = AD.plan_apply(dr["snap"], dr["prop"], d, set(dr["fields"]), sid, sh, ly, st)
        except AD.ApplyError as ex:
            toast(f"Nothing was applied. {ex}", C["amber"]); dr_tick(); return
        try:
            SH.apply_settings(S.song, sh, plan.changes)         # the analyzed sheet, by id; never the lyrics
        except SH.SheetError as ex:
            toast(str(ex), C["amber"]); return
        dr["undo"] = {"song": sid, "sheet": sh, "previous": plan.previous, "name": d.name}
        dr_rebase(sid, sh, ly)                                   # the draft stays usable: it now describes the sheet as applied
        dr["fields"] = AD.default_selection(dr["snap"], dr["prop"], d)
        mark_dirty(); build_settings()
        toast(f"Applied “{d.name}”: Style mode is now Advanced. Generate uses this caption. Restore Previous Settings undoes it.", C["green"])

    def dr_restore(e=None):
        u = dr["undo"]
        if not u or not S.song or S.song.id != u["song"]:
            toast("The song changed. Nothing was restored.", C["amber"]); return
        try:
            SH.apply_settings(S.song, u["sheet"], u["previous"])
        except SH.SheetError as ex:
            toast(str(ex), C["amber"]); return
        dr["undo"] = None
        if S.song.book.active_id == u["sheet"]:
            dr_rebase(S.song.id, u["sheet"], lyr())
        mark_dirty(); build_settings()
        toast("Previous settings restored.", C["green"])

    def dr_head(t: str):
        return ft.Text(t, size=11, weight=ft.FontWeight.BOLD, color=C["text3"])

    def dr_card(d):
        on = dr["sel"] == d.index - 1
        return ft.Container(ft.Column([ft.Row([ft.Container(ft.Text(str(d.index), size=11, weight=ft.FontWeight.BOLD, color="#FFFFFF"), width=20, height=20, border_radius=10,
                                                            bgcolor=C["accent"] if on else C["border"], alignment=ft.Alignment(0, 0)),
                                               ft.Text(d.name, size=13, weight=ft.FontWeight.W_700, color=C["text1"], expand=True)], spacing=8),
                                       ft.Text(d.reason, size=11, color=C["text2"]),
                                       *([ft.Text(d.characteristics, size=11, color=C["text3"], italic=True)] if d.characteristics else [])], spacing=4),
                            padding=10, border_radius=10, ink=True, bgcolor=DR_SEL if on else DR_CARD, border=ft.Border.all(1, C["accent"] if on else C["border"]),
                            on_click=lambda e, i=d.index - 1: dr_pick(i), tooltip="Preview this direction (nothing is changed)")

    def dr_chip(name: str, label: str, value: str, mine):
        on = name in dr["fields"]
        clash = name in ("genre", "mood", "vocal", "energy") and bool(mine) and dr_show(name, mine) != value
        return ft.Container(ft.Row([*([ft.Icon(ft.Icons.CHECK_ROUNDED, size=13, color="#FFFFFF")] if on else []),
                                    ft.Text(f"{label}: {value}", size=11, color="#FFFFFF" if on else C["text1"], no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS)], spacing=3, tight=True),
                            height=26, padding=po(8, 0, 8, 0), border_radius=13, ink=True, bgcolor=C["accent"] if on else DR_CARD,
                            border=ft.Border.all(1, C["amber"] if clash else (C["accent"] if on else C["border"])),
                            tooltip=(f"You chose {dr_show(name, mine)} for this. " if clash else "") + ("Ticked: Apply writes it. Click to untick." if on else "Not ticked: Apply leaves it alone. Click to tick."),
                            on_click=lambda e, n=name: dr_toggle(n))

    def dr_render():
        ctl = [ft.Row([ft.Text("✨ AI Music Director", size=14, weight=ft.FontWeight.W_700, color=C["text1"], expand=True),
                       ft.ProgressRing(width=16, height=16, stroke_width=2, visible=dr["busy"])], vertical_alignment=ft.CrossAxisAlignment.CENTER)]
        ctl.append(ft.Text("Reads your lyrics and settings and suggests three music directions. Your lyrics are never changed, and nothing is applied until you press Apply to Settings.",
                           size=11, color=C["text3"]))
        ctl.append(ft.FilledButton("Analyzing…" if dr["busy"] else "Analyze Lyrics", icon=ft.Icons.AUTO_AWESOME_ROUNDED, disabled=dr["busy"], on_click=dr_start, height=38,
                                   style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF")))
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
        d = prop.directions[dr["sel"]]
        snap = dr["snap"]
        why = dr_stale()
        dr_stale_txt.value, dr_stale_txt.visible = why, bool(why)
        dr_apply_btn.disabled = bool(why) or dr["busy"]
        ctl.append(dr_stale_txt)
        ctl.append(dr_head("EMOTIONAL ANALYSIS"))
        ctl.append(ft.Container(ft.Column([ft.Text(spans=[ft.TextSpan(k.title() + ": ", style=ft.TextStyle(weight=ft.FontWeight.W_600, color=C["text2"])),
                                                                   ft.TextSpan(v, style=ft.TextStyle(color=C["text1"]))], size=11) for k, v in prop.analysis.items()], spacing=3),
                                padding=10, border_radius=10, bgcolor=DR_BG, border=ft.Border.all(1, C["border"])))
        ctl.append(dr_head("AI DIRECTIONS"))
        ctl += [dr_card(x) for x in prop.directions]
        ctl.append(dr_head("ACE-STEP CAPTION PREVIEW"))
        ctl.append(ft.Container(ft.Text(d.caption, size=12, color=C["text1"], selectable=True), padding=10, border_radius=10, bgcolor=DR_BG, border=ft.Border.all(1, C["border"])))
        sug = AD.suggested(prop, d)
        chips = [dr_chip("style_prompt", "Caption", "✓" if "style_prompt" in dr["fields"] else "—", None)]
        for n in ("genre", "mood", "vocal", "energy", "bpm", "keyscale", "timesignature", "duration", "language"):
            if n in sug:
                chips.append(dr_chip(n, AD.LABELS.get(n, n).replace(" (s)", "").replace("Vocal Style", "Vocal").replace("Time Signature", "Time"), dr_show(n, sug[n]), AD.user_value(snap, n)))
        ctl.append(dr_head("SUGGESTED SETTINGS  (tick what Apply should write)"))
        ctl.append(ft.Row(chips, wrap=True, spacing=6, run_spacing=6))
        probs = AD.problems_of(prop, d)
        for f, raw, why_ in probs:
            ctl.append(ft.Text(f"⚠ {AD.LABELS.get(f, f)}: the AI wrote “{str(raw)[:40]}” ({why_}); it is not applied.", size=11, color=C["amber"]))
        for w in d.warnings + prop.warnings + d.notes:
            ctl.append(ft.Text("⚠ " + w, size=11, color=C["amber"]))
        if prop.cues:
            ctl.append(dr_head("SECTION CUE SUGGESTIONS  (ideas only: nothing is inserted into your lyrics)"))
            ctl.append(ft.Container(ft.Column([ft.Text(f"{c.section}  ·  {c.cue}" + (f"  —  {c.why}" if c.why else ""), size=11, color=C["text2"]) for c in prop.cues], spacing=3),
                                    padding=10, border_radius=10, bgcolor=DR_BG, border=ft.Border.all(1, C["border"])))
        ai_box.controls = ctl
        safe_update(ai_box)
        dr_side_buttons()

    def dr_side_buttons():
        """Regenerate / Apply / Restore are small icon buttons beside Generate. They appear only on the AI Recommend tab, and only when there is a draft (Restore: after an Apply)."""
        on = style_tab["v"] == "ai" and dr["prop"] is not None
        dr_regen_btn.visible = dr_apply_btn.visible = on
        dr_regen_btn.disabled = dr["busy"]
        dr_restore_btn.visible = on and bool(dr["undo"])
        dr_restore_btn.tooltip = f"Restore Previous Settings (before “{dr['undo']['name']}”)" if dr["undo"] else "Restore Previous Settings"
        safe_update(dr_regen_btn, dr_apply_btn, dr_restore_btn)

    def _dr_icon(icon, tip, fn, bg=None):
        return ft.IconButton(icon, icon_size=22, width=46, height=46, padding=0, tooltip=tip, on_click=fn, visible=False, icon_color="#FFFFFF" if bg else C["text1"], bgcolor=bg or C["panel2"],
                             style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10)))
    dr_regen_btn = _dr_icon(ft.Icons.REFRESH_ROUNDED, "Regenerate Suggestions (analyze the lyrics again)", dr_start)
    dr_apply_btn = _dr_icon(ft.Icons.CHECK_ROUNDED, "Apply to Settings", dr_apply, C["green"])
    dr_restore_btn = _dr_icon(ft.Icons.UNDO_ROUNDED, "Restore Previous Settings", dr_restore)

    shared_box = ft.Column(spacing=8, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)                        # Model, BPM, Key, Time, Language, Duration, Seed: shown on the Basic and Advanced tabs
    source_note = ft.Text("", size=11, color=C["text3"])
    style_tab = {"v": "basic"}
    side_hooks: list = []                                   # called when the sidebar width changes (apply_widths)
    style_box_ref = {"c": None}

    def show_style_tab(t: str):
        """Basic / Advanced / AI Recommend are three views of the Style panel. Only Basic and Advanced are a style_mode; AI Recommend is just a view and never changes it."""
        style_tab["v"] = t
        ai = t == "ai"
        basic_box.visible = t == "basic"
        adv_box.visible = t == "advanced"
        shared_box.visible = not ai
        adv_tile.visible = not ai
        ai_box.visible = ai
        for key, btn in mode_buttons.items():
            on = key == t
            btn.bgcolor = C["accent"] if on else "#00000000"
            btn.content.color = "#FFFFFF" if on else C["text2"]
            btn.content.weight = ft.FontWeight.W_600 if on else ft.FontWeight.NORMAL
        mode = S.song.settings.style_mode if S.song else "basic"
        source_note.value = {"basic": "AI creates a music caption from your settings and lyrics.", "advanced": "Write and control the music caption yourself.",
                             "ai": "Let AI suggest musical directions for your lyrics."}[t]
        source_note.tooltip = ("Active Caption Source: Advanced. The Music Style Prompt is sent as written; the Basic tags are kept but not sent." if mode == "advanced"
                               else "Active Caption Source: Basic. The AI writes the caption from genre, mood, vocal, energy, extra tags and your lyrics; the Advanced prompt is kept but not sent.")
        dr_side_buttons()
        if style_box_ref["c"] is not None:                    # the AI Recommend tab is long: it gets most of the sidebar height (Output Takes stay below, smaller)
            style_box_ref["c"].expand = 6 if ai else 5
            if style_box_ref.get("o") is not None:
                style_box_ref["o"].expand = 2 if ai else 3                 # the Output Takes keep a useful share (about three takes) below the settings
            safe_update(style_box_ref["c"], style_box_ref.get("o"))
        safe_update(basic_box, adv_box, shared_box, adv_tile, ai_box, source_note, *mode_buttons.values())
        try:
            render_basic_caption()
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
        rows = [[f["bpm"], f["key"], f["time"]], [f["dur"], f["lang"], f["seed"]]] if three else [[f["bpm"], f["key"]], [f["time"], f["dur"]], [f["lang"], f["seed"]]]
        meta_body.controls = [ft.Row(r, spacing=8, vertical_alignment=ft.CrossAxisAlignment.START) for r in rows]
        safe_update(meta_body)
    side_hooks.append(layout_meta)

    def build_settings():
        if not S.song:
            settings_col.controls = [ft.Text("Open or create a song", size=12, color=C["text3"])]
            adv_tile.controls = []
            safe_update(settings_col, adv_tile)
            return
        s = S.song.settings
        names = refresh_workflows()
        wf_dd = dd("Model", [(n, "ACE-Step 1.5 (ComfyUI)" if n == names[0] and len(names) == 1 else n) for n in names] or [("", "No workflow found")], s.workflow or "", sset("workflow"), None)
        wf_dd.expand = True
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
        basic_box.controls = [ui_card(ft.Icons.MUSIC_NOTE_OUTLINED, "Style Settings", [ft.Row([genre], spacing=8), ft.Row([mood, vocal], spacing=8), ft.Row([energy], spacing=8), extra_tf])]
        prompt_tf.value = s.style_prompt
        adv_box.controls = [prompt_tf, history_tile]
        meta_ctl["f"] = {"bpm": bpm_tf, "key": key_dd, "time": ts_dd, "dur": dur_tf, "lang": lang, "seed": ft.Row([seed_tf, ibtn(ft.Icons.CASINO_OUTLINED, "Random seed", dice, 18, w=32)], spacing=2, expand=True)}
        layout_meta()
        shared_box.controls = [ui_card(ft.Icons.VIEW_IN_AR_OUTLINED, "Model", [ft.Row([wf_dd, ibtn(ft.Icons.REFRESH_ROUNDED, "Rescan workflows", lambda e: build_settings(), 18, w=34)], spacing=4)]),
                               ui_card(ft.Icons.TUNE_ROUNDED, "Song Metadata", [meta_body])]
        settings_col.controls = [mode_row, source_note, basic_box, adv_box, shared_box, ai_box]
        adv_tile.controls = [ft.Row([
            dd("Seed mode", [("same", "Both seeds equal"), ("fixed", "KSampler own seed")], s.seed_mode, sset("seed_mode"), 170),
            tfield("KSampler seed", s.ksampler_seed, sset("ksampler_seed", int, 0, 2**32 - 1), 120, number=True),
            tfield("Steps", s.steps, sset("steps", int, 1, 100), 76, number=True),
            tfield("CFG", s.cfg, sset("cfg", float, 0.0, 30.0), 76, number=True),
            tfield("Text CFG", s.text_cfg, sset("text_cfg", float, 0.0, 30.0), 84, number=True),
            tfield("Temperature", s.temperature, sset("temperature", float, 0.0, 2.0), 100, number=True),
            tfield("Top P", s.top_p, sset("top_p", float, 0.0, 1.0), 76, number=True),
            tfield("Top K", s.top_k, sset("top_k", int, 0, 1000), 76, number=True),
            tfield("Min P", s.min_p, sset("min_p", float, 0.0, 1.0), 76, number=True),
        ], wrap=True, spacing=8, run_spacing=8), ft.Text("Output format: MP3 • BPM, key, language and duration are also sent to the model as separate parameters", size=11, color=C["text3"])]
        show_style_tab(style_tab["v"] if style_tab["v"] == "ai" else (s.style_mode if s.style_mode in ("basic", "advanced") else "basic"))
        rebuild_history()
        safe_update(settings_col, adv_tile)

    def style_tags(s: M.SongSettings) -> str:
        return M.style_tags(s)

    # ═══ EDITOR: generation + output ════════════════════════════════════════════════════════════════════════
    btn_generate = ft.FilledButton("Generate", icon=ft.Icons.MUSIC_NOTE_ROUNDED, height=46, expand=True, on_click=lambda e: page.run_task(generate),
                                   style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF", shape=ft.RoundedRectangleBorder(radius=10), text_style=ft.TextStyle(size=15, weight=ft.FontWeight.BOLD)))
    gen_status = ft.Text("", size=12, color=C["text2"])
    output_title = ft.Text("Output", size=17, weight=ft.FontWeight.BOLD, color=C["text1"])
    output_count = ft.Text("", size=12, color=C["text3"])
    output_list = ft.ListView(expand=True, spacing=1, padding=po(0, 0, 4, 0))

    def set_gen(status: str, **kw):
        if S.gen is not None:
            S.gen["status"] = status
            S.gen.update(kw)
        rebuild_output()
        update_generate_button()

    def update_generate_button():
        busy = S.gen is not None and S.gen.get("status") in ("captioning", "queued", "running")
        btn_generate.disabled = busy or S.song is None
        btn_generate.content = "Creating Caption…" if (busy and S.gen.get("status") == "captioning") else "Generating…" if busy else "Generate"
        safe_update(btn_generate)

    def take_row(song_id: str, title: str, t: M.Take, registry: list, queue_fn, allow_delete=True, on_changed=None, show_title=False, play_fn=None, compact=False):
        pw = 28 if compact else 40
        pbtn = ft.IconButton(ft.Icons.PAUSE_ROUNDED if player.is_playing_take(t.id) else ft.Icons.PLAY_ARROW_ROUNDED, icon_size=17 if compact else 22, width=pw, height=pw, padding=0,
                             icon_color="#FFFFFF", bgcolor=C["accent"], tooltip="Play / pause",
                             on_click=(lambda e: play_fn()) if play_fn else (lambda e: play_take(song_id, title, t, queue_fn)))
        registry.append((t.id, pbtn))
        star = ft.IconButton(ft.Icons.STAR_ROUNDED if t.favorite else ft.Icons.STAR_BORDER_ROUNDED, icon_size=16 if compact else 19, width=26 if compact else 30, height=26 if compact else 30, padding=0,
                             icon_color=C["amber"] if t.favorite else C["text3"], tooltip="Favorite",
                             on_click=lambda e: toggle_fav(song_id, t, on_changed))
        items = [ft.PopupMenuItem(content=ft.Text("View Generation Settings"), on_click=lambda e: view_take_settings(title, t)),
                 ft.PopupMenuItem(content=ft.Text("Open as Editable Sheet"), on_click=lambda e: open_take_as_sheet(song_id, t)),
                 ft.PopupMenuItem(content=ft.Text("Export Audio"), on_click=lambda e: export_take(title, t))]
        if allow_delete:
            items.append(ft.PopupMenuItem(content=ft.Text("Delete take…"), on_click=lambda e: delete_take_confirm(song_id, t, on_changed)))
        menu = ft.PopupMenuButton(icon=ft.Icons.MORE_VERT_ROUNDED, icon_size=16 if compact else 18, icon_color=C["text2"], items=items)
        playing = player.is_current(t.id)
        dur_txt = fmt_dur(t.duration) if t.duration and t.duration > 0 else "length unknown"
        sub = f"{dur_txt} · {short_date(t.created)[5:]}" if compact else f"{dur_txt}  {short_date(t.created)}"
        name = f"{title} · Take {t.number:02d}" if show_title else f"Take {t.number:02d}"
        wide_enough = (not compact) or side_inner_w() >= 270                   # a narrow sidebar: Export Audio stays in the ⋮ menu only
        actions = [pbtn] + ([ibtn(ft.Icons.FILE_DOWNLOAD_OUTLINED, "Export Audio", lambda e: export_take(title, t), 16 if compact else 18, w=26 if compact else 30)] if wide_enough else []) + [star, menu]
        if compact:                                                            # one small line per take: more of the list is visible at once
            return ft.Container(padding=po(left=4, right=0, top=1, bottom=1), border_radius=8, bgcolor=C["accent_soft"] if playing else "#00000000",
                                content=ft.Row([cover_box(song_id, 28), ft.Row([ft.Text(name, size=12, weight=ft.FontWeight.W_600, color=C["text1"], no_wrap=True),
                                                                                ft.Text(sub, size=10, color=C["text3"], no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, expand=True)], spacing=6, expand=True,
                                                               vertical_alignment=ft.CrossAxisAlignment.CENTER), *actions], spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER))
        return ft.Container(padding=po(left=8, right=2, top=6, bottom=6), border_radius=10, bgcolor=C["accent_soft"] if playing else "#00000000",
                            content=ft.Row([cover_box(song_id, 44), ft.Column([ft.Text(name, size=14, weight=ft.FontWeight.W_600, color=C["text1"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                                                                               ft.Text(sub, size=11, color=C["text3"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)], spacing=0, expand=True),
                                            *actions], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER))

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
                                     content=ft.Row([ft.ProgressRing(width=20, height=20, stroke_width=3, color=C["accent"]), ft.Text(label, size=13, color=C["text1"], expand=True),
                                                     ft.TextButton("Cancel", on_click=lambda e: request_cancel(), disabled=g["status"] == "cancelling")], spacing=10)))
        if s and g and g["song_id"] != s.id and g["status"] in ("captioning", "queued", "running"):
            rows.append(ft.Text("Another song is being generated. Its result will appear in that song.", size=11, color=C["text3"]))
        for t in takes:
            rows.append(take_row(s.id, s.title, t, reg_output, lambda: queue_for_song(s.id, takes[::-1]), on_changed=reload_takes, compact=True))
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
        page.show_dialog(ft.AlertDialog(title=ft.Text(f"{title} · Take {t.number:02d}"), bgcolor=C["panel"], content=ft.Container(width=520, height=420, content=ft.Column(
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

    # Basic mode: the AI-enhanced Music Caption (basic_caption.py). The widgets show the caption that was really sent for the open song.
    basic_cap_view = {"song": "", "text": "", "basic": "", "src": ""}
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
        """The caption that is really sent. Basic: the one written for the last Generate of this song (the AI's, or the plain Basic tags the user chose as the fallback). Advanced: the active prompt."""
        song = S.song
        if not song or style_tab["v"] == "ai":
            cap_card.visible = False
            safe_update(cap_card)
            return
        if song.settings.style_mode == "advanced":
            text, src = (song.settings.style_prompt or "").strip(), "Your Music Style Prompt"
            empty = "Write a Music Style Prompt (Advanced tab) to see what is sent."
        else:
            ok = basic_cap_view["song"] == song.id and bool(basic_cap_view["text"])
            text, src = (basic_cap_view["text"], "Written by the AI from your Basic settings and lyrics" if basic_cap_view["src"] == "ai" else "Plain Basic tags (the fallback you chose)") if ok else ("", "")
            empty = "Generate a song to view the effective caption."
        cap_text.value = text or empty
        cap_text.color = C["text1"] if text else C["text3"]
        cap_src.value = src
        cap_copy.visible = bool(text)
        cap_card.visible = True
        safe_update(cap_card, cap_text, cap_src, cap_copy)

    async def ask_use_basic_tags(problems: list, tags: str) -> bool:
        """The AI caption failed: say so and let the user choose. Nothing is sent to ACE-Step until the answer is Use Basic Tags."""
        fut = asyncio.get_running_loop().create_future()

        def done(v: bool):
            page.pop_dialog()
            if not fut.done():
                fut.set_result(v)
        body = ft.Column([*[ft.Text("• " + p, size=12, color=C["text2"]) for p in problems[:5]],
                          ft.Text("You can generate with the plain Basic tags instead (the old way, without an AI-written caption):", size=12, color=C["text1"]),
                          ft.Text(tags, size=12, color=C["text2"], selectable=True, italic=True)], spacing=6, tight=True)
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Text("The AI Music Caption could not be created"), content=ft.Container(body, width=480),
                                        actions=[ft.TextButton("Cancel", on_click=lambda e: done(False)),
                                                 ft.FilledButton("Use Basic Tags", on_click=lambda e: done(True), style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF"))]))
        return await fut

    async def basic_caption_for(cap_snap, basic_tags: str, gen: dict):
        """The caption to generate with, or None (cancelled / the user declined the fallback). Runs the AI off the UI thread; the lyrics stay editable meanwhile."""
        backend, host = S.backend, S.host
        err = None
        caption = None
        if not backend:
            err = BC.CaptionError("No AI backend is available.", ["No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab."])
        else:
            S.chat_busy_song = cap_snap.song_id
            try:
                res = await asyncio.get_running_loop().run_in_executor(None, lambda: BC.compose_basic_caption(cap_snap, dr_ask_fn(backend, host)))
                caption = res.caption
            except BC.CaptionError as ex:
                err = ex
                log.debug("basic caption refused: %s\nRAW:\n%s", ex.problems, ex.raw)
            except Exception as ex:
                err = BC.CaptionError(f"AI error: {ex}")
            finally:
                S.chat_busy_song = ""
        if gen.get("cancel"):
            return None
        if err is not None:
            return basic_tags if await ask_use_basic_tags(err.problems, basic_tags) else None
        return caption

    async def generate():
        if not S.song:
            return
        if S.gen and S.gen["status"] in ("captioning", "queued", "running", "cancelling"):
            toast("A song is already being generated"); return
        song = S.song
        s = song.settings
        comp = K.compile_lyrics(song.lyrics)
        tags = style_tags(s)
        instrumental = comp.ace.strip().lower() == "[instrumental]"                  # an instrumental song: the lyrics field is just the [Instrumental] tag (as the tutorial describes)
        if not comp.ace.strip() or (not instrumental and not any(k in ("text", "hold") for k, _ in comp.lines)):
            toast("Write some lyrics first (or only [Instrumental] for a song without singing)", C["amber"]); return
        if not tags:
            toast("Write the Music Style Prompt first" if s.style_mode == "advanced" else "Choose a Genre first", C["amber"]); return
        if not s.workflow:
            toast("No ACE-Step workflow in comfy_apis/audio. Export one in API format and put it there.", C["red"]); return
        SH.flush(song)                                            # the active sheet is brought up to date before anything is captured
        snap = M.song_from_dict(M.song_to_dict(song))             # the snapshot of THIS song: nothing after this point reads the editor
        sheet = SH.active_sheet(song)
        before = SH.settings_to_dict(song.settings)               # the options exactly as the user had them (seed -1, language "auto"), before they are resolved
        song_id, title = snap.id, snap.title
        if s.style_mode != "advanced":                            # Basic: the LLM writes ONE music caption from the Basic choices and the lyrics (nothing is applied to the sheet)
            if S.chat_busy_song or S.an_busy or tp["busy"] or dr["busy"]:
                toast("The AI is still busy with another request. Try again in a moment.", C["amber"]); return
            cap_snap = BC.make_snapshot(song_id, sheet.id, title, song.lyrics, snap.settings)
            S.gen = {"song_id": song_id, "status": "captioning", "pid": "", "cancel": False}
            update_generate_button(); rebuild_output()
            caption = await basic_caption_for(cap_snap, tags, S.gen)
            if S.gen is None or S.gen.get("cancel") or caption is None:
                S.gen = None
                update_generate_button(); rebuild_output(); return
            S.gen = None
            basic_cap_view["song"], basic_cap_view["text"], basic_cap_view["basic"] = song_id, caption, tags
            basic_cap_view["src"] = "basic" if caption == tags else "ai"
            render_basic_caption()
            tags = caption                                        # the effective caption: what G.prepare / G.execute send and what the Take stores
        try:
            wf, used = G.prepare(AUDIO_API_DIR, snap, tags, comp.ace)
        except Exception as ex:
            toast(f"Cannot prepare the workflow: {ex}", C["red"]); return
        gen_snapshot = TS.snapshot_extra(sheet.id, sheet.name, song.lyrics, before, snap.settings.language)
        if s.style_mode == "advanced" and S.song is not None and S.song.id == song_id and M.add_prompt_history(s, tags):      # the prompt that is really sent is remembered (no duplicates)
            mark_dirty(); rebuild_history()
        if S.song is not None and S.song.id == song_id:
            save_song_now()
        S.gen = {"song_id": song_id, "status": "queued", "pid": "", "cancel": False}
        update_generate_button(); rebuild_output()
        loop = asyncio.get_running_loop()
        gen = S.gen

        def on_state(st):
            if st == "unreachable":
                gen["note"] = "ComfyUI is not responding…"              # shown in the Output panel; the wait gives up on its own after ~30 s
                return
            gen["note"] = ""
            if st == "running" and gen["status"] != "cancelling":
                gen["status"] = "running"
        res = await loop.run_in_executor(None, lambda: G.execute(S.host, snap, wf, used, tags, comp.ace, lambda: gen["cancel"], on_state, lambda pid: gen.update(pid=pid), gen_snapshot))
        S.gen = None
        refresh_lib()
        if S.song and S.song.id == song_id:
            S.takes, S.runs = ST.load_takes(song_id)
        update_generate_button(); rebuild_output(); rebuild_song_list(); rebuild_lib_page()
        if res.status == "completed":
            toast(f"“{title}”: Take {res.take.number:02d} is ready", C["green"])
        elif res.status == "cancelled":
            toast("Generation cancelled", C["amber"])
        else:
            toast(f"Generation failed: {res.error}", C["red"])

    # ═══ EDITOR: AI chat ════════════════════════════════════════════════════════════════════════════════════
    chat_list = ft.ListView(expand=True, spacing=8, auto_scroll=True, padding=po(0, 0, 6, 0))
    chat_tf = ft.TextField(hint_text="Ask the AI to write or change lyrics...", multiline=True, min_lines=1, max_lines=3, shift_enter=True, on_submit=lambda e: page.run_task(send_chat),
                           **field_style(expand=True, height=None, hint_style=ft.TextStyle(color=C["text3"])))
    chat_ctx = ft.Text("", size=11, color=C["text3"])
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
            dd_.on_select = on_backend_pick

    def on_backend_pick(e):
        S.backend = e.control.value
        backend_dd.value = an_backend_dd.value = S.backend
        cfg.update({"chat_backend": S.backend}); _save_cfg(cfg)
        safe_update(backend_dd, an_backend_dd)
        render_analysis()
    init_backend_dd()

    def refresh_chat_ctx():
        a, b = S.sel
        v = f"Selected: {b - a} chars (the AI changes only these)" if (S.song and b > a) else "No selection: the AI sees the whole lyrics"
        if v != chat_ctx.value:
            chat_ctx.value = v
            safe_update(chat_ctx)

    def bubble(i: int, m: M.ChatMessage):
        sid = S.song.id if S.song else ""
        if m.role == "user":
            return ft.Row([ft.Container(expand=True), ft.Container(ft.Text(m.text, size=13, color="#FFFFFF", selectable=True), bgcolor=C["accent"], padding=po(12, 8, 12, 8), border_radius=12, width=None)],
                          alignment=ft.MainAxisAlignment.END)
        if m.role == "note":
            return ft.Text(m.text, size=11, color=C["amber"], selectable=True)
        body = [ft.Text(m.text, size=13, color=C["text1"], selectable=True)]
        if m.action:                                            # a result of a Workspace -> AI Actions button: say which action, and on which part of the song
            lab = (m.action_req or {}).get("label") or AC.AI_ACTIONS.get(m.action, {}).get("label", "")
            body = [ft.Text("AI ACTION RESULT", size=11, weight=ft.FontWeight.BOLD, color=C["amber"]), ft.Text(lab, size=11, color=C["text2"], selectable=True)] + body
            if m.proposal:
                note = {"all": "This proposal covers the ENTIRE song: Apply replaces all of it (Undo brings it back).",
                        "insert": "Only new lines: Apply adds them after that line and replaces nothing."}.get(m.scope, "")
                if note:
                    body.append(ft.Text(note, size=11, color=C["amber"]))
        if m.proposal:
            body.append(ft.Container(ft.Text(m.proposal, size=12, color=C["text1"], selectable=True, font_family="Consolas"), bgcolor=C["field"], padding=10, border_radius=8,
                                     border=ft.Border.all(1, C["border"])))
            if m.applied:
                body.append(ft.Text("✓ Applied", size=11, color=C["green"]))
            body.append(ft.Row([ft.IconButton(ft.Icons.CHECK_ROUNDED, icon_size=18, icon_color="#FFFFFF", bgcolor=C["accent"], width=34, height=34, padding=0, tooltip="Apply to Editor",
                                                  on_click=lambda e, k=m.id, s=sid: apply_proposal(k, s), disabled=m.applied),
                                ibtn(ft.Icons.CONTENT_COPY_ROUNDED, "Copy", lambda e, t=m.proposal: page.run_task(copy_text, t, "Proposal")),
                                ibtn(ft.Icons.REFRESH_ROUNDED, "Regenerate", lambda e, k=m.id, s=sid: page.run_task(regenerate, k, s))], spacing=4))
        return ft.Container(ft.Column(body, spacing=6), bgcolor=C["panel2"], padding=po(12, 8, 12, 8), border_radius=12)

    def rebuild_chat():
        if not S.song:
            chat_list.controls = [ft.Text("Open or create a song to chat about its lyrics.", size=12, color=C["text3"])]
        elif not S.chat:
            chat_list.controls = [ft.Text("Ask for a new verse, a stronger chorus, a different mood, or how to stretch a Thai word. Nothing changes until you press Apply.", size=12, color=C["text3"])]
        else:
            chat_list.controls = [bubble(i, m) for i, m in enumerate(S.chat)]
        safe_update(chat_list)
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

    async def ask_ai(instruction: str):
        if not S.song:
            return
        if S.chat_busy_song:
            toast("The AI is still answering"); return
        if not S.backend:
            toast("No AI backend: put an LLM workflow in comfy_apis/llm or set a Grok key in Prompt Lab", C["amber"]); return
        song = S.song
        sheet0 = song.book.active_id                            # the proposal belongs to THIS sheet, whatever sheet is open when the answer arrives
        text = song.lyrics
        a, b = S.sel
        has_sel = 0 <= a < b <= len(text)
        selected = text[a:b] if has_sel else ""
        user_msg = LC.build_user_message(song.title, {"genre": song.settings.genre, "mood": song.settings.mood, "language": K.detect_language(text) if (song.settings.language or "auto") == "auto" else song.settings.language, "bpm": song.settings.bpm},
                                         text, selected, instruction, [(m.role, m.text) for m in S.chat if m.role in ("user", "assistant")])
        S.chat_busy_song = song.id
        chat_busy.visible = True; safe_update(chat_busy)
        loop = asyncio.get_running_loop()
        try:
            if S.backend == "grok":
                g = LC.grok_creds()
                raw = await loop.run_in_executor(None, LC.ask_grok, g["key"], g["model"], LC.SYSTEM_PROMPT, user_msg)
            else:
                raw = await loop.run_in_executor(None, LC.ask_local, S.host, LLM_DIR / S.backend.split(":", 1)[1], LC.SYSTEM_PROMPT, user_msg)
            reply, proposal = LC.parse_answer(raw)
            append_chat(song.id, M.ChatMessage("assistant", reply, proposal=proposal, scope="selection" if has_sel else "all", snap_text=selected if has_sel else text,
                                               snap_start=a if has_sel else 0, snap_end=b if has_sel else len(text), sheet_id=sheet0))
        except LC.LLMFormatError as ex:
            append_chat(song.id, M.ChatMessage("note", f"The AI answer did not follow the format ({ex}). Nothing was changed. Try again."))
        except Exception as ex:
            append_chat(song.id, M.ChatMessage("note", f"AI error: {ex}"))
        finally:
            S.chat_busy_song = ""
            chat_busy.visible = False; safe_update(chat_busy)

    async def send_chat(e=None):
        txt = (chat_tf.value or "").strip()
        if not txt or not S.song:
            return
        chat_tf.value = ""; safe_update(chat_tf)
        append_chat(S.song.id, M.ChatMessage("user", txt))
        await ask_ai(txt)

    # ═══ AI Actions (Workspace): Continue / Increase Emotion / Improve Singability / Translate. Same transport, parser, chat proposal and Apply as the chat. ═══════════════════════════
    def action_counts(sess):
        """{file line: EFFECTIVE syllable count} of the active song when its syllable result is current (Phase 1-2, manual corrections included); None otherwise. Never recounted."""
        info = sess.analysis if sess else None
        if not (info and "result" in info and info["snap"].text_hash == LA.text_hash(lyr())):
            return None
        effs, _ = LA.effective_lines(info["result"], sess.an_corr)
        return {e.line.file_line: e.count for e in effs}

    def refresh_actions_ui():
        busy = bool(S.chat_busy_song) or bool(S.an_busy)
        for card in action_cards:
            card.opacity = 0.5 if busy else 1.0
        act_ring.visible = bool(S.chat_busy_song)
        act_status.value = "The AI is working… the proposal will appear in the Chat tab." if S.chat_busy_song else ""
        safe_update(*action_cards, act_ring, act_status)

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
                                             action_counts(sess) if req.action == "singability" else None)
        if not regen:
            append_chat(song_id, M.ChatMessage("user", label))
        pick_ai_tab("chat")
        S.chat_busy_song = song_id
        chat_busy.visible = True; safe_update(chat_busy)
        refresh_actions_ui()
        loop = asyncio.get_running_loop()
        backend, host = S.backend, S.host
        proposal = ""
        try:
            if backend == "grok":
                g = LC.grok_creds()
                raw = await loop.run_in_executor(None, LC.ask_grok, g["key"], g["model"], system, user_msg)
            else:
                raw = await loop.run_in_executor(None, LC.ask_local, host, LLM_DIR / backend.split(":", 1)[1], system, user_msg)
            reply, proposal = LC.parse_answer(raw)
            log.debug("ai action %s raw: %r", req.action, raw)
            if not proposal.strip():
                append_chat(song_id, M.ChatMessage("assistant", reply))                     # the AI declined: its reply is shown, there is nothing to apply
            else:
                prop = AC.check_proposal(req, proposal, text)
                scope = "insert" if req.mode == "insert" else ("all" if req.mode == "all" else "selection")
                d = req.to_dict(); d["label"] = label
                append_chat(song_id, M.ChatMessage("assistant", reply, proposal=prop, scope=scope, snap_text=req.target, snap_start=req.start, snap_end=req.end,
                                                   action=req.action, action_req=d, snap_hash=req.lyrics_hash, sheet_id=sheet0))
        except LC.LLMFormatError as ex:
            append_chat(song_id, M.ChatMessage("note", f"The AI answer did not follow the format ({ex}). Nothing was changed. Try again."))
        except AC.ActionError as ex:
            shown = proposal.strip()                            # a refused proposal is still shown (it can be read and copied), but it is never offered for Apply
            append_chat(song_id, M.ChatMessage("note", str(ex) + (f"\n\nWhat the AI returned (not applied, you can copy it):\n{shown[:1800]}{'…' if len(shown) > 1800 else ''}" if shown else "")))
        except Exception as ex:
            append_chat(song_id, M.ChatMessage("note", f"AI error: {ex}"))
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
        hint = ft.Text("Section tags such as [Verse] and [Chorus - powerful] stay as they are. The result appears in the Chat; nothing changes until you press Apply.", size=11, color=C["text3"])

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

    def clear_chat(e=None):
        if not S.song or not S.chat:
            return

        def do():
            S.chat = []
            if not ST.clear_chat_file(S.song.id):
                toast("The chat file could not be cleared", C["red"])
            rebuild_chat()
        confirm("Clear chat", "Delete the chat history of this song?", "Clear", do, danger=True)

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
                if state["cid"] != cur.song_id:
                    cover.content = cover_box(cur.song_id, 44)
                    state["cid"] = cur.song_id
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

    async def player_tick():
        while True:
            await asyncio.sleep(0.4)
            try:
                player.tick()
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
                                                             ft.dropdown.Option("takes", "Most Takes")], on_select=lambda e: (setattr(S, "sort", e.control.value), rebuild_lib_page()), **field_style(height=38))
    lib_search = ft.TextField(hint_text="Search songs...", prefix_icon=ft.Icons.SEARCH_ROUNDED, on_change=lambda e: (setattr(S, "search", e.control.value or ""), rebuild_lib_page(), rebuild_song_list()),
                              **field_style(width=260, height=38, hint_style=ft.TextStyle(color=C["text3"])))

    def source_tracks() -> list:
        """The Song List: [(song, take)] of the selected source, in display order. Takes only come from the library, which lists takes whose audio file exists."""
        src = S.source
        if src["type"] == "project":
            x = lib_entry(src["id"])
            if x:
                return [(x["song"], t) for t in sorted(x["takes"], key=lambda t: t.number, reverse=True)]
            S.source = {"type": "all", "id": None}              # the project is gone
        out = [(x["song"], t) for x in S.lib for t in x["takes"] if S.source["type"] == "all" or t.favorite]
        out.sort(key=lambda p: (p[1].created, p[1].number), reverse=True)
        return out

    def source_label() -> str:
        src = S.source
        if src["type"] == "project":
            x = lib_entry(src["id"])
            return x["song"].title if x else "All Songs"
        return COLLECTIONS[src["type"]][0]

    def select_source(kind: str, song_id: str | None = None):
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

    def rebuild_tracks():
        reg_libtakes.clear()
        tr = source_tracks()
        tracks_title.value = f"{source_label()} · {len(tr)} track{'s' if len(tr) != 1 else ''}"
        proj = S.source["type"] == "project"
        rows = [take_row(s.id, s.title, t, reg_libtakes, None, on_changed=reload_takes, show_title=not proj, play_fn=lambda t=t: play_from_song_list(t)) for s, t in tr]
        if not rows:
            if proj:
                rows = [ft.Text("No tracks generated yet.", size=13, color=C["text3"])]
            else:
                rows = [ft.Column([ft.Text("No tracks available.", size=14, weight=ft.FontWeight.W_600, color=C["text2"])] +
                                  ([ft.Text("Star a take to add it to Favorites.", size=12, color=C["text3"])] if S.source["type"] == "favorites" else []), spacing=4)]
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

    def rebuild_lib_page():
        vis = filtered_lib()
        n_all = sum(len(x["takes"]) for x in S.lib)
        n_fav = sum(1 for x in S.lib for t in x["takes"] if t.favorite)
        cards_row.controls = [collection_card("all", n_all), collection_card("favorites", n_fav)]
        projects_list.controls = [project_card(x) for x in vis] or [ft.Text("Nothing matches your search." if S.search.strip() else "No songs yet. Press New Song to start.", size=13, color=C["text3"])]
        lib_count.value = f"{len(vis)} song{'s' if len(vis) != 1 else ''}"
        safe_update(cards_row, projects_list, lib_count)
        rebuild_tracks()
        rebuild_queue_panel()

    # ═══ SHELL ══════════════════════════════════════════════════════════════════════════════════════════════
    conn_dot = ft.Container(width=9, height=9, border_radius=5, bgcolor=C["red"])
    conn_txt = ft.Text("ComfyUI not connected", size=12, color=C["text2"])
    page_holder = ft.Container(expand=True)
    tabs_row = ft.Row(spacing=4, scroll=ft.ScrollMode.AUTO, expand=True, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def set_tab(key: str):
        if key == "editor" and not S.song:
            key = "library"
        if key == "library":
            park()                                               # the editor's controls leave the page: remember this song's caret and scroll position
        S.tab = key
        page_holder.content = library_page if key == "library" else editor_page
        refresh_lib()
        if key == "library":
            rebuild_lib_page()
        else:
            rebuild_song_list()
        render_tabs()
        safe_update(page_holder)

    def host_dialog(e=None):
        tf = ft.TextField(label="ComfyUI address", value=S.host, **field_style(height=48))

        def do(ev):
            S.host = (tf.value or "").strip() or "127.0.0.1:8188"
            cfg["host"] = S.host; _save_cfg(cfg); page.pop_dialog(); init_backend_dd(); safe_update(backend_dd, an_backend_dd)
        page.show_dialog(ft.AlertDialog(title=ft.Text("ComfyUI connection"), content=tf, bgcolor=C["panel"], actions=[ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()), ft.FilledButton("Save", on_click=do)]))

    def open_library_folder(e=None):
        try: os.startfile(str(ST.library_dir()))
        except Exception as ex: toast(f"Cannot open: {ex}", C["red"])

    top_bar = ft.Container(bgcolor=C["top"], border=ft.Border(bottom=ft.BorderSide(2, C["accent"])), padding=po(left=8, right=14, top=0, bottom=0),
                           content=ft.Row([tabs_row, conn_dot, conn_txt, ibtn(ft.Icons.SETTINGS_OUTLINED, "ComfyUI connection", host_dialog, 20), ibtn(ft.Icons.FOLDER_OPEN_ROUNDED, "Open the music folder", open_library_folder, 20)],
                                          spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER))

    def panel(content, **kw):
        return ft.Container(content=content, bgcolor=C["panel"], border_radius=12, border=ft.Border.all(1, C["border"]), **kw)

    def divider_v(on_drag):
        return ft.GestureDetector(content=ft.Container(width=8, bgcolor="#00000000", content=ft.Container(width=2, bgcolor=C["border"], margin=po(3, 0, 3, 0))), drag_interval=15,
                                  on_horizontal_drag_update=on_drag, mouse_cursor=ft.MouseCursor.RESIZE_LEFT_RIGHT)

    def divider_h(on_drag, on_end=None):
        return ft.GestureDetector(content=ft.Container(height=8, bgcolor="#00000000", content=ft.Container(height=2, bgcolor=C["border"], margin=po(0, 3, 0, 3))), drag_interval=15,
                                  on_vertical_drag_update=on_drag, on_vertical_drag_end=on_end, mouse_cursor=ft.MouseCursor.RESIZE_UP_DOWN)

    # editor page columns --------------------------------------------------------------------------------------
    MIN_EDITOR_W, TOOLS_W, STRIP_W = 380, 300, 44                   # TOOLS_W: the width of the Lyrics Tools column inside the Workspace "Lyrics Tools" tab

    def seg_buttons(options, current, on_pick):
        return [ft.Container(ft.Text(t, size=12, weight=ft.FontWeight.W_600, color="#FFFFFF" if k == current else C["text2"]), padding=po(12, 6, 12, 6), border_radius=8,
                             bgcolor=C["accent"] if k == current else C["field"], ink=True, on_click=lambda e, k=k: on_pick(k)) for k, t in options]

    def persist_layout():
        cfg.update({"ai_w": S.ai_w, "side_w": S.side_w, "side_open": S.side_open, "ws_open": S.ws_open, "ws_tab": S.ws_tab, "ws_h": S.ws_h, "tools_open": S.tools_user})
        _save_cfg(cfg)

    def coming_soon(lines: list):
        return ft.Column([ft.Row([ft.Icon(ft.Icons.HOURGLASS_EMPTY_ROUNDED, size=14, color=C["amber"]), ft.Text("Coming Soon", size=12, weight=ft.FontWeight.BOLD, color=C["amber"])], spacing=6)] +
                         [ft.Text("•  " + ln, size=12, color=C["text2"]) for ln in lines], spacing=4)

    # AI Assistant (left): the existing chat controls, moved; Analysis is only a container for later ------------------------------
    chat_input_row = ft.Row([chat_tf, ft.IconButton(ft.Icons.SEND_ROUNDED, icon_color="#FFFFFF", bgcolor=C["accent"], on_click=lambda e: page.run_task(send_chat), width=42, height=42, tooltip="Send")],
                            spacing=8, vertical_alignment=ft.CrossAxisAlignment.END)
    backend_dd.width = None
    backend_dd.expand = True
    chat_view = ft.Column([ft.Row([ft.Text("Model", size=11, color=C["text3"]), backend_dd], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                           ft.Container(chat_list, expand=True), chat_ctx, chat_input_row], spacing=6, expand=True)
    # Lyrics Analysis (syllables: the AI splits, Python validates and counts; Phase 2: the user's manual corrections, kept apart from the raw AI result) ----
    an_cache = LA.ResultCache()
    rh_cache = LA.ResultCache()
    rt_cache = LA.ResultCache()
    th_cache = LA.ResultCache()
    an_state = {"fresh": None}
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

    def an_identity(backend: str) -> tuple:
        """(model identity, request parameters) that decide whether a saved answer may be reused. A local workflow is identified by its file name AND a hash of its content
        (so another model inside the same file name is a different answer); Grok by its model name."""
        if backend == "grok":
            return LC.grok_creds()["model"], "t=0.7,max=4096,effort=low"
        wf = LLM_DIR / backend.split(":", 1)[1]
        try:
            ident = f"{wf.name}@{LA.text_hash(wf.read_text(encoding='utf-8'))[:10]}"
        except Exception:
            ident = wf.name + "@unreadable"
        c = LC.load_cfg()
        return ident, f"t={c.get('temperature', 0.7)},max={c.get('max_tokens', 4096)}"

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

    def _an_info():
        sess = sessions.get(S.song.id) if S.song else None
        return sess.analysis if sess else None

    _SRC = {"ai": "≈ AI estimate", "units": "Manual segmentation", "count": "Manual count"}

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
        return ft.Container(ft.Column(body, spacing=2), bgcolor=C["panel2"], border_radius=8, padding=po(8, 6, 4, 6), border=ft.Border.all(1, C["amber"]) if e.manual else None)

    def render_analysis():
        """Show the analysis of the ACTIVE song. Read-only for the lyrics: nothing here touches the lyrics field, its selection or its scroll."""
        sess = sessions.get(S.song.id) if S.song else None
        info = sess.analysis if sess else None
        busy_here = bool(S.song) and S.an_busy == S.song.id and S.an_kind == "syl"
        cur = LA.text_hash(lyr()) if S.song else ""
        has_result = bool(info and "result" in info)
        fresh = has_result and info["snap"].text_hash == cur
        an_state["fresh"] = fresh if has_result else None
        err = (info or {}).get("last_error")
        err_here = bool(err and err.get("text_hash") == cur)
        an_ring.visible = busy_here
        an_btn.disabled = (not S.song) or bool(S.an_busy)
        an_reask.visible, an_reask.disabled = has_result, an_btn.disabled
        an_err.value = (f"The last analysis failed: {err['message']}" + (" The earlier result below is kept." if has_result else "")) if err_here else ""
        an_err.visible = bool(an_err.value)
        an_warn.value = ("The lyrics changed after this analysis. Press Analyze Lyrics to update it. Your manual corrections are kept and apply again only to lines that still match."
                         if (has_result and not fresh) else "")
        an_warn.visible = bool(an_warn.value)
        rows, summary, status, note = [], "", "", ""
        if busy_here:
            status = S.an_progress or "Asking the AI…"
        if has_result:
            res: LA.Analysis = info["result"]
            effs, inactive = LA.effective_lines(res, sess.an_corr)
            rows = [_an_row(e, editable=fresh and not S.an_busy) for e in effs]
            n_manual = sum(1 for e in effs if e.manual)
            summary = f"Total: {LA.effective_total(effs)} syllables · {len(effs)} lines" + (f" · {n_manual} edited by you" if n_manual else "")
            if not busy_here:
                status = f"{info['snap'].backend.replace('local:', 'Local: ')} · {info.get('at', '')}" + (" · saved result" if (info.get("cached") or info.get("loaded")) else "") + ("" if fresh else " · outdated")
            if fresh and inactive:
                note = f"{len(inactive)} manual correction(s) belong to lines that are not in the lyrics now. They are kept and are not applied to any other line."
        elif err_here and not busy_here:
            status = "The analysis failed"
        if not rows and not busy_here and not an_err.value:
            rows = [ft.Text("Press Analyze Lyrics: the AI splits every sung line into syllables and says which language it hears. Section tags like [Verse] are skipped. "
                            "The count is made here from the syllables it returns, so it is an estimate: use Edit to correct a line.", size=12, color=C["text3"])]
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
            if info and "result" in info and (cur == info["snap"].text_hash) != an_state.get(key):
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
        model, params = an_identity(backend)
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
                toast("Showing the saved result: no new AI request."); return
        S.an_busy, S.an_kind, S.an_progress = song_id, "syl", "Asking the AI…"
        render_analysis()
        loop = asyncio.get_running_loop()

        def ask(system: str, user: str) -> str:
            if backend == "grok":
                g = LC.grok_creds()
                return LC.ask_grok(g["key"], g["model"], system, user)
            return LC.ask_local(host, LLM_DIR / backend.split(":", 1)[1], system, user)

        def progress(k: int, n: int):
            S.an_progress = f"Asking the AI… (part {k} of {n})" if n > 1 else "Asking the AI…"
            loop.call_soon_threadsafe(render_analysis)
        result, error = None, ""
        try:
            result = await loop.run_in_executor(None, lambda: LA.analyze(lines, ask, progress=progress))
        except LA.AnalysisError as ex:
            error = str(ex)
            log.debug("analysis failed: %s | raw=%r", ex, ex.raw)
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
    rh_list = ft.ListView(expand=True, spacing=6, padding=po(0, 0, 4, 0))
    rh_btn = ft.FilledButton("Analyze Rhymes", icon=ft.Icons.COMPARE_ARROWS_ROUNDED, height=36, on_click=lambda e: page.run_task(run_rhymes, False),
                             style=ft.ButtonStyle(bgcolor=C["accent"], color="#FFFFFF", shape=ft.RoundedRectangleBorder(radius=8)))
    rh_reask = ibtn(ft.Icons.REFRESH_ROUNDED, "Refresh: ask the AI again (does not use the saved result)", lambda e: page.run_task(run_rhymes, True), 18)
    rh_reask.visible = False
    _RH_COLOR = {"end": C["accent"], "internal": C["green"], "near": C["amber"]}

    def _rh_card(p: "LR.RhymePair", texts: list):
        a_line, a_word, b_line, b_word = (p.a_line, p.a_word, p.b_line, p.b_word) if p.a_line <= p.b_line else (p.b_line, p.b_word, p.a_line, p.a_word)
        langs = " + ".join(LANG_NAMES.get(c, c.upper()) for c in p.lang.split("+"))
        conf_col = C["green"] if p.confidence == "high" else (C["amber"] if p.confidence == "medium" else C["red"])

        def side(num, word):
            return ft.Column([ft.Text(f"Line {num}: {word}", size=13, weight=ft.FontWeight.BOLD, color=C["text1"], selectable=True),
                              ft.Text(texts[num - 1] if 0 < num <= len(texts) else "", size=11, color=C["text3"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)], spacing=0)
        if a_line == b_line:
            body = [ft.Text(f"Line {a_line}", size=11, color=C["text3"]), ft.Text(f"{a_word}  ↔  {b_word}", size=13, weight=ft.FontWeight.BOLD, color=C["text1"], selectable=True),
                    ft.Text(texts[a_line - 1] if 0 < a_line <= len(texts) else "", size=11, color=C["text3"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)]
        else:
            body = [side(a_line, a_word), ft.Text("↕", size=14, color=_RH_COLOR[p.type]), side(b_line, b_word)]
        body.append(ft.Row([ft.Text(LR.TYPE_TITLES[p.type], size=11, weight=ft.FontWeight.W_600, color=_RH_COLOR[p.type]), ft.Text(f"· {langs} ·", size=11, color=C["text3"]),
                            ft.Text(p.confidence, size=11, weight=ft.FontWeight.W_600, color=conf_col)], spacing=4))
        if p.reason:
            body.append(ft.Text("Explanation: " + p.reason, size=11, color=C["text2"], selectable=True))
        return ft.Container(ft.Column(body, spacing=2), bgcolor=C["panel2"], border_radius=8, padding=po(8, 6, 8, 6), border=ft.Border.all(1, C["amber"]) if p.confidence in ("low", "uncertain") else None)

    def render_rhymes():
        """Show the rhyme analysis of the ACTIVE song (read-only: nothing here touches the lyrics field, its selection or its scroll)."""
        sess = sessions.get(S.song.id) if S.song else None
        info = sess.rhyme if sess else None
        busy_here = bool(S.song) and S.an_busy == S.song.id and S.an_kind == "rhyme"
        cur = LA.text_hash(lyr()) if S.song else ""
        has_result = bool(info and "result" in info)
        fresh = has_result and info["snap"].text_hash == cur
        an_state["rh_fresh"] = fresh if has_result else None
        err = (info or {}).get("last_error")
        err_here = bool(err and err.get("text_hash") == cur)
        rh_ring.visible = busy_here
        rh_btn.disabled = (not S.song) or bool(S.an_busy)
        rh_reask.visible, rh_reask.disabled = has_result, rh_btn.disabled
        rh_err.value = (f"The last rhyme analysis failed: {err['message']}" + (" The earlier result below is kept." if has_result else "")) if err_here else ""
        rh_err.visible = bool(rh_err.value)
        rh_warn.value = "The lyrics changed after this analysis. Press Analyze Rhymes to update it. The pairs below belong to the old lyrics." if (has_result and not fresh) else ""
        rh_warn.visible = bool(rh_warn.value)
        rows, summary, status, note = [], "", "", ""
        if busy_here:
            status = S.an_progress or "Asking the AI…"
        if has_result:
            res: LR.RhymeResult = info["result"]
            for t, title in (("end", "END RHYMES"), ("internal", "INTERNAL RHYMES"), ("near", "NEAR RHYMES")):
                rows.append(ft.Text(title, size=11, weight=ft.FontWeight.BOLD, color=_RH_COLOR[t]))
                ps = res.of_type(t)
                rows += [_rh_card(p, res.lines) for p in ps] if ps else [ft.Text("No matches found", size=11, color=C["text3"])]
            summary = (f"{len(res.pairs)} rhyme pair(s) found" if res.pairs else "The AI found no rhymes") + " · an AI judgement of the sound, not a proof"
            if not busy_here:
                status = f"{info['snap'].backend.replace('local:', 'Local: ')} · {info.get('at', '')}" + (" · saved result" if (info.get("cached") or info.get("loaded")) else "") + ("" if fresh else " · outdated")
            if res.ignored:
                note = f"{len(res.ignored)} suggestion(s) from the AI were ignored because they did not match your lyrics:\n" + "\n".join("• " + m for m in res.ignored[:6])
        elif err_here and not busy_here:
            status = "The rhyme analysis failed"
        if not rows and not busy_here and not rh_err.value:
            rows = [ft.Text("Press Analyze Rhymes: the AI looks for end, internal and near rhymes by how the words SOUND, in any language. Not every line rhymes, and nothing is invented to "
                            "fill the list. This only reads your lyrics; it never changes them.", size=12, color=C["text3"])]
        rh_list.controls = rows
        rh_note.value, rh_note.visible = note, bool(note)
        rh_summary.value, rh_status.value = summary, status
        safe_update(rh_ring, rh_btn, rh_reask, rh_err, rh_warn, rh_note, rh_list, rh_summary, rh_status)

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
        model, params = an_identity(backend)
        snap = LA.Snapshot(song_id, text, _ui["epoch"], backend, model, f"{params},w={LR.WINDOW_LINES}/{LR.WINDOW_OVERLAP}", LR.RHYME_PROMPT_VERSION, kind=LR.KIND)
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
                toast("Showing the saved result: no new AI request."); return
        S.an_busy, S.an_kind, S.an_progress = song_id, "rhyme", "Asking the AI…"
        render_analysis()
        loop = asyncio.get_running_loop()

        def ask(system: str, user: str) -> str:
            if backend == "grok":
                g = LC.grok_creds()
                return LC.ask_grok(g["key"], g["model"], system, user)
            return LC.ask_local(host, LLM_DIR / backend.split(":", 1)[1], system, user)        # a fresh session every call: never the Chat's or the syllable analysis' history

        def progress(k: int, n: int):
            S.an_progress = f"Asking the AI… (part {k} of {n})" if n > 1 else "Asking the AI…"
            loop.call_soon_threadsafe(render_analysis)
        result, error = None, ""
        try:
            result = await loop.run_in_executor(None, lambda: LR.analyze_rhymes(lines, ask, progress=progress))
        except LA.AnalysisError as ex:
            error = str(ex)
            log.debug("rhyme analysis failed: %s | raw=%r", ex, ex.raw)
        except Exception as ex:
            error = f"AI error: {ex}"
            log.debug("rhyme analysis AI error: %r", ex)
        finally:
            S.an_busy, S.an_kind, S.an_progress = "", "", ""
        if result is not None:
            log.debug("rhyme raw answers: %r | parsed: %r | ignored: %r", result.raw, [(p.type, p.a_line, p.a_word, p.b_line, p.b_word, p.confidence) for p in result.pairs], result.ignored)
            rh_cache.put(snap.cache_key, result)                        # only a COMPLETE, validated result is ever cached: a failed window raises before this point
        sess = sessions.get(snap.song_id)
        if sess is None:
            render_analysis(); return                                    # the tab was closed meanwhile
        if sess.song.lyrics != snap.text:                                # edited while waiting: this answer describes OLD lyrics
            log.debug("rhyme analysis for %s dropped: lyrics changed while waiting", snap.song_id)
            if S.song and S.song.id == snap.song_id:
                toast("The lyrics changed while the AI was working, so that result was not used. Press Analyze Rhymes again.", C["amber"])
            render_analysis(); return
        if result is None:
            failed(error); return
        sess.rhyme = {"snap": snap, "result": result, "at": at}
        persist_analysis(sess)
        render_analysis()

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
        rt_btn.disabled = (not S.song) or bool(S.an_busy) or rl is None
        rt_reask.visible, rt_reask.disabled = has_result, rt_btn.disabled
        rt_err.value = (f"The last rhythm analysis failed: {err['message']}" + (" The earlier result below is kept." if has_result else "")) if err_here else ""
        rt_err.visible = bool(rt_err.value)
        why = ""
        if has_result and not fresh:
            same_text = info["snap"].text_hash == cur
            why = ("The syllable counts changed after this analysis (the lyrics did not). " if same_text else "The lyrics changed after this analysis. ") + \
                  "Press Analyze Rhythm to update it. The AI remarks below belong to the older version."
        rt_warn.value, rt_warn.visible = why, bool(why)
        rows, status, note, summary = [], "", "", ""
        if busy_here:
            status = S.an_progress or "Asking the AI…"
        if rl is None:
            rows = [ft.Text("Analyze Syllables First: the rhythm comparison uses the syllable counts (and your manual corrections). Open the Syllables tab and press Analyze Lyrics. "
                            "Nothing is counted again by the AI here.", size=12, color=C["amber"])]
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
        if rl is None:
            toast("Analyze Syllables First: the rhythm analysis needs the syllable counts of these lyrics.", C["amber"]); return
        ch = RT.counts_hash(rl)
        model, params = an_identity(backend)
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
                toast("Showing the saved result: no new AI request."); return
        S.an_busy, S.an_kind, S.an_progress = song_id, "rhythm", "Asking the AI…"
        render_analysis()
        loop = asyncio.get_running_loop()

        def ask(system: str, user: str) -> str:
            if backend == "grok":
                g = LC.grok_creds()
                return LC.ask_grok(g["key"], g["model"], system, user)
            return LC.ask_local(host, LLM_DIR / backend.split(":", 1)[1], system, user)        # a fresh session every call: never the Chat's, syllable or rhyme history

        def progress(k: int, n: int):
            S.an_progress = f"Asking the AI… (part {k} of {n})" if n > 1 else "Asking the AI…"
            loop.call_soon_threadsafe(render_analysis)
        result, error = None, ""
        try:
            result = await loop.run_in_executor(None, lambda: RT.analyze_rhythm(rl, ask, progress=progress))
        except LA.AnalysisError as ex:
            error = str(ex)
            log.debug("rhythm analysis failed: %s | raw=%r", ex, ex.raw)
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

    def thes_fresh(sess) -> bool:
        info = sess.thes if sess else None
        if not (info and "result" in info and info["snap"].text_hash == LA.text_hash(lyr())):
            return False
        r = info["result"]
        v = TH.validate_selection(lyr(), r.start, r.end)
        return isinstance(v, TH.Selection) and v.phrase == r.phrase and TH.build_context(lyr(), v, th_line_count(sess, v)).hash == r.ctx_hash

    def th_capture(kind, sel):
        """Called for every selection event of the lyrics field. Only the user's own selections count: a caret (empty selection) clears it. Read-only."""
        if kind != "user" or not S.song:
            return
        sess = sessions.get(S.song.id)
        if sess is None:
            return
        a, b = sel
        sess.thes_sel = {"start": a, "end": b, "text": lyr()[a:b]} if b > a else None
        if S.ai_tab == "analysis" and an_sub["k"] == "thes":
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

    def _th_card(n: int, it):
        head = ft.Row([ft.Text(f"{n}.", size=11, color=C["text3"]), ft.Text(it.text, size=14, weight=ft.FontWeight.BOLD, color=C["text1"], selectable=True, expand=True),
                       ft.IconButton(ft.Icons.FIND_REPLACE_ROUNDED, icon_size=20, icon_color="#FFFFFF", bgcolor=C["accent"], width=34, height=34, padding=0, tooltip="Replace the selected phrase with this",
                                     on_click=lambda e, i=it: th_replace(i))],
                      spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        body = [head, ft.Row([ft.Text(TH.TYPE_TITLES[it.type], size=11, weight=ft.FontWeight.W_600, color=C["accent"]), ft.Text("· AI Suggestion", size=11, color=C["amber"])], spacing=4),
                ft.Text(it.explanation, size=11, color=C["text2"], selectable=True),
                ft.Text("Meaning difference: " + it.difference, size=11, color=C["text2"], selectable=True)]
        if it.singability:
            body.append(ft.Text("Singability: " + it.singability, size=11, color=C["text3"], selectable=True))
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
        th_warn.value = ("The lyrics, or the syllable count of this line, changed after these suggestions were made. They belong to the older version." if (has_result and not fresh) else "")
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
                rows.append(ft.Text(f"{len(res.ignored)} suggestion(s) were ignored:\n" + "\n".join("• " + m for m in res.ignored[:5]), size=11, color=C["text3"], selectable=True))
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
        ctx = TH.build_context(text, sel, th_line_count(sess, sel))
        model, params = an_identity(backend)
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
                toast("Showing the saved result: no new AI request."); return
        S.an_busy, S.an_kind, S.an_progress = song_id, "thes", "Asking the AI…"
        render_analysis()
        loop = asyncio.get_running_loop()

        def ask(system: str, user: str) -> str:
            if backend == "grok":
                g = LC.grok_creds()
                return LC.ask_grok(g["key"], g["model"], system, user)
            return LC.ask_local(host, LLM_DIR / backend.split(":", 1)[1], system, user)        # a fresh session every call: never the Chat's or another analysis' history
        result, error = None, ""
        try:
            result = await loop.run_in_executor(None, lambda: TH.suggest(sel, ctx, ask))
        except LA.AnalysisError as ex:
            error = str(ex)
            log.debug("thesaurus failed: %s | raw=%r", ex, ex.raw)
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
            same = isinstance(v, TH.Selection) and v.phrase == sel.phrase and TH.build_context(sess.song.lyrics, v, th_line_count(sess, v, sess.song.lyrics)).hash == ctx.hash
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
    rh_view = ft.Column([ft.Row([rh_btn, rh_reask, rh_ring, rh_status], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                         rh_warn, rh_err, ft.Container(rh_list, expand=True), rh_note, rh_summary], spacing=6, expand=True, visible=False)

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
    analysis_view = ft.Column([ft.Row([ft.Text("AI Backend", size=11, color=C["text3"]), an_backend_dd], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                               an_sub_row, syl_view, rh_view, rt_view, th_view], visible=False, spacing=6, expand=True)
    ai_tabs_row = ft.Row(spacing=4)

    def pick_ai_tab(k: str):
        S.ai_tab = k
        ai_tabs_row.controls = seg_buttons([("chat", "Chat"), ("analysis", "Analysis")], k, pick_ai_tab)
        chat_view.visible, analysis_view.visible = k == "chat", k == "analysis"
        safe_update(ai_tabs_row, chat_view, analysis_view)
    ai_tabs_row.controls = seg_buttons([("chat", "Chat"), ("analysis", "Analysis")], "chat", pick_ai_tab)
    ai_col = panel(ft.Column([ft.Row([ft.Text("Songwriting Assistant", size=14, weight=ft.FontWeight.BOLD, color=C["text1"]), chat_busy, ft.Container(expand=True),
                                      ibtn(ft.Icons.DELETE_SWEEP_OUTLINED, "Clear this song's chat", clear_chat)], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                              ai_tabs_row, chat_view, analysis_view], spacing=8, expand=True), padding=12)
    ai_holder = ft.Container(content=ai_col, width=S.ai_w)

    # Lyrics Editor (centre) with the Lyrics Tools to its right -------------------------------------------------------------------
    def toggle_tools(e=None):
        """The Lyrics Tools live in the Workspace ("Lyrics Tools" tab, after AI Actions): this opens that tab, or collapses the Workspace when it is already showing it."""
        if S.ws_open and S.ws_tab == "tools":
            S.ws_open = False
        else:
            S.ws_open, S.ws_tab = True, "tools"
        refresh_ws(); fit_workspace_height(); persist_layout()
    btn_tools = ibtn(ft.Icons.BUILD_CIRCLE_OUTLINED, "Open the Lyrics Tools (Workspace tab)", toggle_tools)
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
        return ft.Column([ft.Row([ribbon_btn(k, ic, color, tip) for k, _lbl, ic, tip in tools], wrap=True, spacing=2, run_spacing=2, width=cols * 38 + 2),
                          ft.Text(title, size=10, color=C["text3"], text_align=ft.TextAlign.CENTER)], spacing=4, horizontal_alignment=ft.CrossAxisAlignment.CENTER)

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
                items.append(ft.Container(width=1, height=96, bgcolor=C["border"], margin=po(0, 2, 0, 0)))
            items.append(g)
        tools_view.content = ft.Column([ft.Row(items, wrap=True, spacing=12, run_spacing=10, vertical_alignment=ft.CrossAxisAlignment.START)], scroll=ft.ScrollMode.AUTO, expand=True)
        safe_update(tools_view)
    ribbon_hook["fn"] = lambda: tools_layout(force=True)
    tools_layout(force=True)
    lyrics_header = ft.Row([ft.Row([ft.Text("Lyrics", size=18, weight=ft.FontWeight.BOLD, color=C["text1"]), title_tf, dirty_txt], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                            ft.Row([btn_tools, ibtn(ft.Icons.LABEL_OUTLINED, "Section Options… for the [tag] at the cursor (Ctrl+Shift+E)", open_section_options), btn_undo, btn_redo, copy_menu, ibtn(ft.Icons.SAVE_OUTLINED, "Save song", save_song_now)], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER)],
                           alignment=ft.MainAxisAlignment.SPACE_BETWEEN, wrap=True, run_spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER)
    lyrics_panel = panel(ft.Column([lyrics_header, ft.Container(drop_target, expand=True), sheet_bar, ft.Row([ft.Container(notes_txt, expand=True), tag_chip, sel_chip], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)], spacing=8, expand=True,
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
    actions_view = ft.Column([ft.Row([*action_cards, structures_card], wrap=True, spacing=8, run_spacing=8), ft.Row([act_ring, act_status], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)],
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
                            tooltip=f"{CUE_CAT.get(c.category, c.category)}: adds “{c.phrase}” to the tag", on_click=None if dis else (lambda e, p=c.phrase: pv_toggle(p, p not in pv["chosen"])))

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
        backend, host = ctx["backend"], ctx["host"]

        def ask(system, user):
            if backend == "grok":
                g = LC.grok_creds()
                return LC.ask_grok(g["key"], g["model"], system, user)
            return LC.ask_local(host, LLM_DIR / backend.split(":", 1)[1], system, user)
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
        page.show_dialog(ft.AlertDialog(modal=True, bgcolor=C["panel"], title=ft.Text("The AI arrangement was not usable"), content=ft.Container(width=520, height=360, content=ft.Column(
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
                                        content=ft.Container(width=760, height=520, content=ft.Column([ft.Text(f"From the sheet “{ctx['sheet_name']}”. Nothing changes until you choose.", size=12, color=C["text2"]), *blocks],
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
        backend, host = ctx["backend"], ctx["host"]

        def ask(system, user):
            if backend == "grok":
                g = LC.grok_creds()
                return LC.ask_grok(g["key"], g["model"], system, user)
            return LC.ask_local(host, LLM_DIR / backend.split(":", 1)[1], system, user)
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
        tl_take_dd.on_select = tl_pick_take
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
    center_col_ui = ft.Column([top_row, ws_divider, workspace_holder], spacing=0, expand=True)

    # Collapsible Right Sidebar: Style / Output ----------------------------------------------------------------------------------
    style_panel = panel(ft.Column([ft.Container(ft.Column([settings_col, adv_tile], spacing=8, scroll=ft.ScrollMode.AUTO), expand=True),
                                   ft.Row([btn_generate, dr_regen_btn, dr_apply_btn, dr_restore_btn], spacing=6), cap_card, gen_status], spacing=8), padding=12)
    output_panel = panel(ft.Column([ft.Row([output_title, ft.Container(expand=True), output_count], vertical_alignment=ft.CrossAxisAlignment.CENTER), ft.Container(output_list, expand=True)], spacing=8, expand=True),
                         padding=12, expand=True)
    style_box = ft.Container(style_panel, expand=3)
    style_box_ref["c"] = style_box
    style_box_ref["o"] = output_panel
    side_expanded = ft.Column([ft.Row([ft.Text("Style / Output", size=14, weight=ft.FontWeight.BOLD, color=C["text1"]), ft.Container(expand=True),
                                       ibtn(ft.Icons.KEYBOARD_DOUBLE_ARROW_RIGHT_ROUNDED, "Hide Style", lambda e: set_sidebar(False))], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                               style_box, ft.Container(height=8), output_panel], spacing=6, expand=True)
    side_strip = panel(ft.Column([ibtn(ft.Icons.KEYBOARD_DOUBLE_ARROW_LEFT_ROUNDED, "Show Style", lambda e: set_sidebar(True)),
                                  ibtn(ft.Icons.TUNE_ROUNDED, "Open Style Settings", lambda e: set_sidebar(True, "style")),
                                  ibtn(ft.Icons.LIBRARY_MUSIC_ROUNDED, "Open Output", lambda e: set_sidebar(True, "output"))], spacing=6, horizontal_alignment=ft.CrossAxisAlignment.CENTER), padding=po(4, 8, 4, 8), expand=True)
    sidebar_col = ft.Column([side_expanded, side_strip], spacing=0, expand=True)
    sidebar_holder = ft.Container(content=sidebar_col, width=S.side_w)
    # When the window is too narrow to dock the sidebar, the same Style / Output panel slides over the editor as a drawer (side_strip stays in the layout).
    drawer_layer = ft.Container(visible=False, right=0, top=0, bottom=0, width=S.side_w, bgcolor=C["bg"], border_radius=12,
                                shadow=ft.BoxShadow(blur_radius=24, color="#99000000"), padding=po(8, 0, 0, 0))

    def page_w() -> float:
        try:
            return float(page.width or page.window.width or 1500)
        except Exception:
            return 1500.0

    def page_h() -> float:
        try:
            return float(page.height or page.window.height or 900)
        except Exception:
            return 900.0

    drawer_state = {"open": False}                              # the drawer is never remembered: a narrow window starts with it closed
    layout = {"side": True, "tools": True, "drawer": False}     # what is really shown now (the user's choices live in S.side_open / S.tools_user)

    def apply_widths():
        """Columns keep their minimum widths: the lyrics editor is served first. When the window is too narrow the Style sidebar collapses by itself, then the tools;
        those automatic collapses are NOT the user's choice (S.side_open / S.tools_user stay as they were), so the layout comes back when the window is wide again."""
        avail = page_w() - 20
        ai = max(240.0, min(360.0, S.ai_w))
        side_w = max(320.0, min(560.0, S.side_w))

        def need(side: bool, tools: bool) -> float:
            return ai + 8 + MIN_EDITOR_W + ((TOOLS_W + 8) if tools else 0) + ((side_w + 8) if side else STRIP_W + 4)
        side, tools = S.side_open, False                                   # the Lyrics Tools are a Workspace tab now: no column
        can_dock = need(True, tools) <= avail
        if side and not can_dock:
            side = False
        if tools and need(side, tools) > avail:
            tools = False
        drawer = (not can_dock) and drawer_state["open"]
        if can_dock:
            drawer_state["open"] = False
        layout["side"], layout["tools"], layout["drawer"] = side, tools, drawer
        ai_holder.width = ai
        # the Style / Output panel lives in the docked column, or in the drawer layer when it does not fit
        if drawer:
            sidebar_col.controls = [side_strip]
            drawer_layer.content = side_expanded
        else:
            drawer_layer.content = None
            sidebar_col.controls = [side_expanded, side_strip]
        drawer_layer.visible = drawer
        drawer_layer.width = min(side_w, max(240.0, avail - 40))
        side_expanded.visible, side_strip.visible = (side or drawer), not side
        side_div.visible = side
        sidebar_holder.width = side_w if side else STRIP_W
        fit_workspace_height()
        for h in side_hooks:
            h()
        safe_update(editor_page)

    def set_sidebar(open_: bool, focus: str | None = None):
        """focus "style" / "output": the part the user asked for gets most of the sidebar's height. Only the share of the two existing panels changes; nothing is rebuilt,
        so the settings, the output list, its scroll position and the generation status stay as they are."""
        avail = page_w() - 20
        tools = False
        can_dock = (max(240.0, min(360.0, S.ai_w)) + 8 + MIN_EDITOR_W + ((TOOLS_W + 8) if tools else 0) + (max(320.0, min(560.0, S.side_w)) + 8)) <= avail
        if can_dock:
            S.side_open = open_
        else:
            drawer_state["open"] = open_
        if open_ and focus:
            style_box.expand, output_panel.expand = (3, 1) if focus == "style" else (1, 3)
            safe_update(style_box, output_panel)
        apply_widths(); persist_layout()

    def on_ai_drag(e):
        S.ai_w = max(240.0, min(360.0, S.ai_w + e.local_delta.x))
        apply_widths()

    def on_side_drag(e):
        S.side_w = max(320.0, min(560.0, S.side_w - e.local_delta.x))
        apply_widths()

    side_div = divider_v(on_side_drag)
    editor_page = ft.Container(padding=10, expand=True, content=ft.Stack([
        ft.Container(left=0, top=0, right=0, bottom=0, content=ft.Row([ai_holder, divider_v(on_ai_drag), ft.Container(center_col_ui, expand=True), side_div, sidebar_holder],
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
                        ST.write_autosave(sess.song)
                    except Exception as ex:
                        dbg_once("autosave", ex)

    # start ------------------------------------------------------------------------------------------------------------
    refresh_lib()
    apply_widths()
    clear_editor()
    set_tab("library")
    update_generate_button()
    page.run_task(check_connection_loop); page.run_task(player_tick); page.run_task(autosave_loop)
    _update_check.start_notice(ft, page, APP_NAME, APP_VERSION)


if __name__ == "__main__":
    ft.app(target=_app_error.guard(ft, main, APP_NAME, APP_VERSION))
