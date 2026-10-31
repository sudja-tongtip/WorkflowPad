"""The AI specialists of the studio: who they are and where their system prompts live.

Three logical roles (they may all run on the same model): Songwriter AI and Song Architect (the two that Analyze Song asks) and Music Director (Generate). The Songwriter AI replaced the Lyric Craft Advisor,
Song Editor and Thai Prosody Expert (`RETIRED`: their saved chats, reports and prompt files are kept and readable, never merged into the new session).
The user is the songwriter; every specialist is an advisor whose proposals the user previews and applies (or not).
Each has an editable Markdown prompt. The bundled defaults are in `prompts/specialists/`; the copies the user edits live in the user data folder (%APPDATA%\\WorkflowPad\\specialists\\prompts)
because the program folder may be read-only. A default is copied there once. When the program is updated, a user copy that was never edited is upgraded to the new default; a copy the user
edited is never overwritten. Reset puts the default back and keeps the old file as a backup.

A prompt is valid when it is UTF-8, not empty and has every required section. An invalid file is reported (never sent as an empty prompt); the last good prompt keeps serving until the file is fixed.
The prompt hash identifies the version of a prompt: a new hash opens a new session generation (see specialist_engine), never a silent change inside a running one.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

BUNDLED = Path(__file__).with_name("prompts") / "specialists"
USER_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "WorkflowPad" / "specialists" / "prompts"
REQUIRED = ("Role", "Primary Goal", "Responsibilities", "Analysis Method", "Quality Standards", "Rules", "Limitations", "Few-shot Examples", "Output Requirements", "Self-check")


@dataclass(frozen=True)
class Specialist:
    id: str
    name: str
    short: str                  # what the user sees in the dropdown tooltip
    file: str
    analyzes: bool = True       # asked by Analyze Song
    ask_for: str = ""            # what to ask this specialist (used to tell the user who to go to when a question is not the asker's field)


SPECIALISTS = (
    Specialist("songwriter", "Songwriter AI", "Writes, analyses and reworks lyrics: wording, meaning, rhymes and the flow of the lines", "songwriter.md",
               ask_for="WRITING lyrics (new lines, filling the sections with words, continuing the song, translating, rewriting a line), what the song means, word choice, imagery, syllable counts, rhymes and how smoothly Thai lines can be sung"),
    Specialist("song_architect", "Song Architect", "Sections, cues, hook and song structure", "song_architect.md",
               ask_for="the order of the sections, adding or moving sections, cues in the section tags, vocal and timing cues, and where the hook and the chorus sit in the structure (the words of any section are the Songwriter AI's)"),
    Specialist("music_director", "Music Director", "Music style and caption for the generator", "music_director.md", analyzes=False,
               ask_for="music genre, instruments, arrangement and the English music caption (the AI Music Director in the Style panel / Generate tab, not in this chat)"),
)
# The three specialists that the Songwriter AI replaced. They are not asked and not shown in the specialist list any more, but their ids stay known: the chats, reports and session files they
# left in the songs are kept as they are and can still be read (an archive, never merged into the Songwriter AI's session), and their prompt files are never deleted or reused.
RETIRED = (
    Specialist("lyric_craft_advisor", "Lyric Craft Advisor", "Word choice, language and imagery of the lyrics", "lyric_craft_advisor.md"),
    Specialist("song_editor", "Song Editor", "Meaning, story continuity and emotional arc", "song_editor.md"),
    Specialist("prosody", "Thai Prosody Expert", "Syllables, rhymes and the flow of the lines", "prosody_expert.md"),
)
BY_ID = {s.id: s for s in SPECIALISTS + RETIRED}
ANALYZE_ORDER = tuple(s.id for s in SPECIALISTS if s.analyzes)
CHAT_IDS = ANALYZE_ORDER                                  # the two the user can talk to in the chat; the Music Director works in Generate
ARCHIVE_IDS = tuple(s.id for s in RETIRED)                # read-only conversations / reports of the specialists that were merged into the Songwriter AI
DEFAULT_CHAT = "songwriter"
# v1 ids and files: the role of both changed (Lyricist -> Lyric Craft Advisor, Hook Specialist -> Song Architect). Their chats and reports are kept under the new id; their OLD system prompts are never reused.
LEGACY_IDS = {"lyricist": "lyric_craft_advisor", "hook": "song_architect"}
LEGACY_FILES = ("lyricist.md", "hook_specialist.md")


def canon(spec_id: str) -> str:
    """The current id of a specialist id that may come from before the roles changed ('' stays '')."""
    return LEGACY_IDS.get(spec_id, spec_id)


LEGACY_BLANK = "lyric_craft_advisor"                       # a chat message with no specialist id is from before specialists: the single assistant of that time


def owner(spec_id: str) -> str:
    """The specialist whose conversation a chat message belongs to: its own id; a message with NO id belongs to the archive of the old single assistant (the Lyric Craft Advisor), never to a conversation of the
    Songwriter AI (read-only there, and never replayed into its session). Messages the program writes now always carry an id."""
    return canon(spec_id) or LEGACY_BLANK


def name_of(spec_id: str) -> str:
    return BY_ID[spec_id].name if spec_id in BY_ID else spec_id


@dataclass
class Prompt:
    id: str
    text: str = ""
    hash: str = ""
    ok: bool = False
    errors: tuple = ()
    path: str = ""


_cache: dict = {}                                           # id -> Prompt of the last file that was valid


def text_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def check(text: str) -> list:
    """What is wrong with a prompt text (empty list = valid)."""
    errors = []
    if not text.strip():
        return ["The prompt is empty."]
    heads = {m.group(1).strip().lower() for m in re.finditer(r"^#\s+(.+?)\s*$", text, re.MULTILINE)}
    for sec in REQUIRED:
        if sec.lower() not in heads:
            errors.append(f"The section “# {sec}” is missing.")
    return errors


def _read(path: Path) -> tuple:
    """(text, error). The file must be UTF-8."""
    try:
        return path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n"), ""
    except FileNotFoundError:
        return "", "The prompt file is missing."
    except UnicodeDecodeError:
        return "", "The file is not UTF-8 text. Save it again as UTF-8."
    except OSError as ex:
        return "", f"The file cannot be read: {ex}"


def _state_file() -> Path:
    return USER_DIR / "defaults.json"


def _load_state() -> dict:
    try:
        return json.loads(_state_file().read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(d: dict) -> None:
    try:
        USER_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _state_file().with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_state_file())
    except OSError:
        pass


def ensure_user_copies(bundled: Path | None = None, user: Path | None = None) -> list:
    """Make sure every specialist has an editable copy. Returns messages about what was done (installed / upgraded). A copy the user edited is never overwritten."""
    bundled, user = bundled or BUNDLED, user or USER_DIR
    done, state = [], _load_state() if user == USER_DIR else {}
    try:
        user.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        return [f"The prompts folder cannot be created: {ex}"]
    for s in SPECIALISTS:
        src, dst = bundled / s.file, user / s.file
        text, err = _read(src)
        if err:
            done.append(f"{s.name}: the bundled default cannot be read ({err})")
            continue
        new_hash = text_hash(text)
        if not dst.exists():
            try:
                dst.write_bytes(text.encode("utf-8"))
                state[s.id] = new_hash
                done.append(f"{s.name}: the default prompt was installed")
            except OSError as ex:
                done.append(f"{s.name}: cannot write the prompt ({ex})")
            continue
        cur, err2 = _read(dst)
        if not err2 and state.get(s.id) and state[s.id] != new_hash and text_hash(cur) == state[s.id]:
            try:                                           # never edited and the default changed: take the new default (the old one is kept as a backup)
                shutil.copyfile(dst, dst.with_name(f"{dst.stem}.{time.strftime('%Y%m%d-%H%M%S')}.bak.md"))
                dst.write_bytes(text.encode("utf-8"))
                state[s.id] = new_hash
                done.append(f"{s.name}: upgraded to the new default prompt")
            except OSError:
                pass
        elif s.id not in state and not err2 and text_hash(cur) == new_hash:
            state[s.id] = new_hash
    for name in LEGACY_FILES:                              # the prompt files of the old roles: kept (never deleted, never reused) under a .legacy name
        old = user / name
        if old.exists():
            try:
                dst = old.with_name(f"{old.stem}.legacy.md")
                if dst.exists():
                    dst = old.with_name(f"{old.stem}.legacy.{time.strftime('%Y%m%d-%H%M%S')}.md")
                old.replace(dst)
                done.append(f"{name}: the prompt of the old role was kept as {dst.name} (it is not used any more)")
            except OSError:
                pass
    if user == USER_DIR:
        _save_state(state)
    return done


def load(spec_id: str, user: Path | None = None, bundled: Path | None = None) -> Prompt:
    """The prompt of one specialist: the user's copy when valid, else the last good one that was served (with the errors), else an invalid Prompt (never an empty text sent as valid)."""
    s = BY_ID[spec_id]
    path = (user or USER_DIR) / s.file
    text, err = _read(path)
    if not path.exists():                                   # the user's copy was deleted (or never made): the bundled default is used, and the copy is made again at the next start
        path = (bundled or BUNDLED) / s.file
        text, err = _read(path)
    errors = [err] if err else check(text)
    if not errors:
        p = Prompt(spec_id, text, text_hash(text), True, (), str(path))
        _cache[spec_id] = p
        return p
    last = _cache.get(spec_id)
    if last is not None and last.ok:
        return Prompt(spec_id, last.text, last.hash, False, tuple(errors), str(path))     # not ok: the caller shows the errors; the last good text is only a reference
    return Prompt(spec_id, "", "", False, tuple(errors), str(path))


def reload_all() -> dict:
    """Re-read every file (after the user edited them). {id: Prompt}."""
    return {s.id: load(s.id) for s in SPECIALISTS}


def reset(spec_id: str, user: Path | None = None, bundled: Path | None = None) -> str:
    """Put the bundled default back. The file that was there is kept as `<name>.<time>.bak.md`. Returns the backup path ('' when there was none)."""
    s = BY_ID[spec_id]
    user, bundled = user or USER_DIR, bundled or BUNDLED
    text, err = _read(bundled / s.file)
    if err:
        raise RuntimeError(f"The bundled default of {s.name} cannot be read: {err}")
    user.mkdir(parents=True, exist_ok=True)
    dst, backup = user / s.file, ""
    if dst.exists():
        b = dst.with_name(f"{dst.stem}.{time.strftime('%Y%m%d-%H%M%S')}.bak.md")
        shutil.copyfile(dst, b)
        backup = str(b)
    dst.write_bytes(text.encode("utf-8"))
    if user == USER_DIR:
        st = _load_state()
        st[spec_id] = text_hash(text)
        _save_state(st)
    _cache.pop(spec_id, None)
    return backup


def effective_system(spec_id: str, extra: str = "") -> tuple:
    """(the system prompt that is really sent, Prompt). The file text plus the program's own blocks (`extra`: output contracts, craft notes). Raises ValueError with the reason when the prompt is not valid."""
    p = load(spec_id)
    if not p.ok:
        raise ValueError(f"The prompt of {name_of(spec_id)} is not valid: " + " ".join(p.errors))
    return (p.text.rstrip() + ("\n\n" + extra.strip() if extra.strip() else "")), p
