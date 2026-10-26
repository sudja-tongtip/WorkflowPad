"""Templates & Presets (Workspace -> Templates): song structure templates, custom lyrics tools and saved AI prompts.

Everything here is DATA (JSON) and pure functions: nothing from a preset is ever executed or evaluated. A preset can only contain text; what a tool does is decided by its `type`
(a fixed list), and a prompt can only use a fixed list of {placeholders} replaced by plain string substitution.
The app applies every result through its existing editing path (set_lyrics: one undo step) or its existing AI Actions path. No UI, no editor, no LLM transport in this file.

Storage: ONE global file, %APPDATA%/WorkflowPad/music_studio_presets.json, shared by all songs (applying a preset only changes the open song). Built-in templates are
code, not data in the file. A damaged file is never overwritten silently: it is copied to a *.corrupt-<time>.json backup first; a damaged ENTRY is skipped and reported while the
other entries still load.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path

from .compiler import TAG_LINE
from .lyrics_tools import Edit, ToolError, _insert_cue, insert_text, line_bounds

SCHEMA_VERSION = 1
NAME_MAX = 60
PLACEHOLDERS = ("selection", "section", "lyrics")
SCOPES = {"selection": "Selected Lyrics", "line": "Current Line", "all": "Entire Lyrics"}
TOOL_TYPES = {"snippet": "Insert Snippet", "wrap": "Wrap Selection"}
CUSTOM_PREFIX = "custom:"                       # key of a custom tool in the Lyrics Tools (built-in keys never contain a colon: no clash is possible)


class PresetError(ValueError):
    """A sentence for the user."""


def preset_file() -> Path:
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "WorkflowPad" / "music_studio_presets.json"


# ── built-in song structures (code, never written to the file, never editable: copy one to change it) ───────────
def _blocks(*tags: str) -> str:
    return "\n\n".join(f"[{t}]" for t in tags)


BUILTIN_TEMPLATES = [
    {"id": "builtin:pop", "name": "Pop", "description": "Verse, pre-chorus and chorus twice, then a bridge and a final chorus.",
     "structure": _blocks("Intro", "Verse 1", "Pre-Chorus", "Chorus", "Verse 2", "Pre-Chorus", "Chorus", "Bridge", "Final Chorus", "Outro")},
    {"id": "builtin:rock", "name": "Rock", "description": "Verses and choruses with a guitar solo before the bridge.",
     "structure": _blocks("Intro", "Verse 1", "Chorus", "Verse 2", "Chorus", "Guitar Solo", "Bridge", "Final Chorus", "Outro")},
    {"id": "builtin:ballad", "name": "Ballad", "description": "Two verses before the first chorus, a bridge, a final chorus.",
     "structure": _blocks("Intro", "Verse 1", "Verse 2", "Chorus", "Bridge", "Final Chorus", "Outro")},
    {"id": "builtin:acoustic", "name": "Acoustic", "description": "A short, simple form.",
     "structure": _blocks("Intro", "Verse 1", "Chorus", "Verse 2", "Chorus", "Outro")},
    # Added later (data only; the ids above never change). Every tag is a section the program knows (Intro, Verse, Pre-Chorus, Chorus, Bridge, Outro, Instrumental, Build, Drop, Breakdown):
    # nothing here is an experimental tag, and no vocal or production instruction is put into a tag.
    {"id": "builtin:alt_rock", "name": "Alternative Rock", "description": "Loud and quiet contrast: a breakdown after the second chorus, then a bridge and a final chorus.",
     "structure": _blocks("Intro", "Verse 1", "Pre-Chorus", "Chorus", "Verse 2", "Chorus", "Breakdown", "Bridge", "Final Chorus", "Outro")},
    {"id": "builtin:indie_pop", "name": "Indie Pop", "description": "A compact form with an instrumental transition before the bridge.",
     "structure": _blocks("Intro", "Verse 1", "Chorus", "Verse 2", "Chorus", "Instrumental", "Bridge", "Final Chorus", "Outro")},
    {"id": "builtin:edm", "name": "EDM", "description": "Energy builds into a drop, twice, with a breakdown in between.",
     "structure": _blocks("Intro", "Verse 1", "Build", "Drop", "Breakdown", "Verse 2", "Build", "Drop", "Outro")},
    {"id": "builtin:rnb", "name": "R&B", "description": "Verse, pre-chorus and chorus, then straight to a bridge and a final chorus.",
     "structure": _blocks("Intro", "Verse 1", "Pre-Chorus", "Chorus", "Verse 2", "Bridge", "Final Chorus", "Outro")},
    {"id": "builtin:hiphop", "name": "Hip-Hop", "description": "Three verses with a repeating hook (the chorus) between them.",
     "structure": _blocks("Intro", "Verse 1", "Chorus", "Verse 2", "Chorus", "Verse 3", "Chorus", "Outro")},
]


# ── data ─────────────────────────────────────────────────────────────────────────────────────────────────────
@dataclass
class SongTemplate:
    id: str
    name: str
    description: str
    structure: str


@dataclass
class CustomTool:
    id: str
    name: str
    type: str                                   # snippet | wrap
    content: str = ""                           # snippet: the text / tag to insert
    prefix: str = ""                            # wrap
    suffix: str = ""
    empty_pair: bool = False                    # wrap with nothing selected: insert prefix+suffix (True) or ask the user to select (False)
    description: str = ""

    @property
    def key(self) -> str:
        return CUSTOM_PREFIX + self.id


@dataclass
class AiPrompt:
    id: str
    name: str
    description: str
    prompt: str
    scope: str = "selection"                    # the DEFAULT scope only: the real target is captured when the prompt is run


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ── validation ───────────────────────────────────────────────────────────────────────────────────────────────
def check_name(name: str, taken: list, what: str = "preset") -> str:
    """The cleaned name, or PresetError. `taken` = the other names of the same kind (compared ignoring case)."""
    n = re.sub(r"\s+", " ", (name or "").strip())
    if not n:
        raise PresetError("Give it a name.")
    if len(n) > NAME_MAX:
        raise PresetError(f"The name is too long (up to {NAME_MAX} characters).")
    if n.casefold() in {t.casefold() for t in taken}:
        raise PresetError(f"A {what} named “{n}” already exists. Choose another name.")
    return n


def check_structure(s: str) -> str:
    """The structure text with trailing spaces removed, or PresetError. Any tag is allowed (no whitelist): the compiler's own warnings still apply once it is inserted."""
    lines = [l.rstrip() for l in (s or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    text = "\n".join(lines).strip("\n")
    if not text.strip():
        raise PresetError("The structure is empty. Add at least one section tag such as [Verse 1].")
    if len(text) > 4000:
        raise PresetError("The structure is too long (up to 4000 characters).")
    if "\x00" in text:
        raise PresetError("The structure contains an invalid character.")
    return text


def structure_has_lyrics(s: str) -> bool:
    return any(l.strip() and not TAG_LINE.match(l) for l in s.split("\n"))


def structure_of(lyrics: str) -> str:
    """The section tags of a song, one per block (only tags, never the lyric lines): the starting point for 'save the structure of this song'."""
    tags = [l.strip() for l in lyrics.split("\n") if TAG_LINE.match(l)]
    return "\n\n".join(tags)


_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f]")


def check_tool(t: CustomTool) -> CustomTool:
    if t.type not in TOOL_TYPES:
        raise PresetError("Unknown tool type.")
    if t.type == "snippet":
        if not t.content.strip():
            raise PresetError("The content is empty: write the text or tag to insert.")
        if len(t.content) > 500 or _CTRL.search(t.content):
            raise PresetError("The content is too long (up to 500 characters) or has an invalid character.")
        t.prefix = t.suffix = ""
        t.empty_pair = False
    else:
        if not (t.prefix or t.suffix):
            raise PresetError("Give a prefix, a suffix or both.")
        if len(t.prefix) > 40 or len(t.suffix) > 40 or "\n" in t.prefix + t.suffix or _CTRL.search(t.prefix + t.suffix):
            raise PresetError("The prefix and suffix must be one short line each (up to 40 characters).")
        t.content = ""
    t.description = (t.description or "").strip()[:200]
    return t


def used_placeholders(prompt: str) -> list:
    return re.findall(r"\{([^{}]*)\}", prompt or "")


def check_prompt(prompt: str) -> str:
    """The prompt text, or PresetError for an empty one, an unknown {placeholder} or a stray brace. Only {selection} {section} {lyrics} exist."""
    p = (prompt or "").strip()
    if not p:
        raise PresetError("The prompt is empty.")
    if len(p) > 2000:
        raise PresetError("The prompt is too long (up to 2000 characters).")
    bad = [x for x in used_placeholders(p) if x not in PLACEHOLDERS]
    if bad:
        raise PresetError("Unknown placeholder " + ", ".join("{" + b + "}" for b in bad) + ". You can use " + ", ".join("{" + x + "}" for x in PLACEHOLDERS) + ".")
    if re.sub(r"\{(?:%s)\}" % "|".join(PLACEHOLDERS), "", p).count("{") + p.count("}") - len(used_placeholders(p)):
        raise PresetError("A { or } is not part of a placeholder. Remove it, or use only " + ", ".join("{" + x + "}" for x in PLACEHOLDERS) + ".")
    return p


def render_prompt(prompt: str, selection: str, section: str, lyrics: str) -> str:
    """Plain string substitution (no expressions, no attribute access, nothing evaluated). {selection} needs real selected text: it is never filled from a guessed cursor."""
    p = check_prompt(prompt)
    used = set(used_placeholders(p))
    if "selection" in used and not (selection or "").strip():
        raise PresetError("This prompt uses {selection}, but no text is selected. Select the words first.")
    values = {"selection": (selection or "").strip(), "section": section or "(no section)", "lyrics": (lyrics or "").strip()}
    return re.sub(r"\{(selection|section|lyrics)\}", lambda m: values[m.group(1)], p)


# ── (de)serialization of one entry ───────────────────────────────────────────────────────────────────────────
def _s(d, k, default=""):
    v = d.get(k, default)
    if not isinstance(v, str):
        raise PresetError(f"“{k}” must be text.")
    return v


def template_from_dict(d) -> SongTemplate:
    return SongTemplate(_id(d), _s(d, "name").strip() or _bad("name"), _s(d, "description"), check_structure(_s(d, "structure")))


def tool_from_dict(d) -> CustomTool:
    t = CustomTool(_id(d), _s(d, "name").strip() or _bad("name"), _s(d, "type"), _s(d, "content"), _s(d, "prefix"), _s(d, "suffix"), bool(d.get("empty_pair", False)), _s(d, "description"))
    return check_tool(t)


def prompt_from_dict(d) -> AiPrompt:
    scope = _s(d, "scope", "selection")
    if scope not in SCOPES:
        raise PresetError("Unknown scope.")
    return AiPrompt(_id(d), _s(d, "name").strip() or _bad("name"), _s(d, "description"), check_prompt(_s(d, "prompt")), scope)


def _bad(what):
    raise PresetError(f"missing {what}")


def _id(d) -> str:
    i = d.get("id")
    if not isinstance(i, str) or not re.fullmatch(r"[A-Za-z0-9_\-]{3,40}", i):
        raise PresetError("missing or invalid id")
    return i


# ── the store ────────────────────────────────────────────────────────────────────────────────────────────────
class PresetStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else preset_file()
        self.templates: list = []
        self.tools: list = []
        self.prompts: list = []
        self.warnings: list = []                # messages for the user: skipped entries, a backed-up damaged file
        self._skipped: dict = {}                # raw entries that are NOT in the active lists (invalid / duplicate id / duplicate name): never used, never dropped
        self._other: dict = {}                  # raw top-level keys this version does not use

    # -- load / save ------------------------------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path | None = None) -> "PresetStore":
        st = cls(path)
        if not st.path.exists():
            return st
        try:
            data = json.loads(st.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except Exception as ex:
            backup = st.path.with_name(st.path.stem + time.strftime(".corrupt-%Y%m%d-%H%M%S") + st.path.suffix)
            try:
                shutil.copy2(st.path, backup)
                st.warnings.append(f"The presets file could not be read ({ex}). It was copied to {backup.name} and presets start empty; the next save will not lose that copy.")
            except Exception:
                st.warnings.append(f"The presets file could not be read ({ex}) and could not be backed up: it was left untouched. Presets are not saved until this is fixed.")
                st._readonly = True
            return st
        for key, lst, conv in (("song_templates", st.templates, template_from_dict), ("custom_tools", st.tools, tool_from_dict), ("ai_prompts", st.prompts, prompt_from_dict)):
            raw = data.get(key, [])
            if not isinstance(raw, list):
                st.warnings.append(f"“{key}” in the presets file is not a list and was ignored.")
                continue
            seen_ids, seen_names = set(), set()
            for i, d in enumerate(raw, 1):
                try:
                    item = conv(d)
                except Exception as ex:
                    st.warnings.append(f"{key} entry {i} was skipped: {ex}")
                    st._skipped.setdefault(key, []).append(d)           # not used, but kept in the file on every later save
                    continue
                if item.id in seen_ids or item.name.casefold() in seen_names:
                    st.warnings.append(f"{key} entry {i} (“{item.name}”) was skipped: duplicate id or name.")
                    st._skipped.setdefault(key, []).append(d)
                    continue
                seen_ids.add(item.id); seen_names.add(item.name.casefold())
                lst.append(item)
        for k, v in data.items():                                        # anything else in the file (unknown keys, a key that is not a list) is carried along as well
            if k in ("song_templates", "custom_tools", "ai_prompts") and isinstance(v, list):
                continue
            if k == "schema_version":
                continue
            st._other[k + "_unreadable" if k in ("song_templates", "custom_tools", "ai_prompts") else k] = v
        return st

    _readonly = False

    def _write(self, templates: list, tools: list, prompts: list) -> None:
        """Atomically write this state (+ every raw entry that was not loaded). Raises on failure; the in-memory lists are NOT touched here."""
        if self._readonly:
            raise PresetError("The presets file is damaged and could not be backed up, so nothing was saved. See the message in the Templates tab.")
        data = dict(self._other)
        data.update({"schema_version": SCHEMA_VERSION, "song_templates": [asdict(t) for t in templates] + list(self._skipped.get("song_templates", [])),
                     "custom_tools": [asdict(t) for t in tools] + list(self._skipped.get("custom_tools", [])),
                     "ai_prompts": [asdict(p) for p in prompts] + list(self._skipped.get("ai_prompts", []))})
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, self.path)                                   # atomic: the old file is replaced whole or not at all
        except OSError as ex:
            raise PresetError(f"The presets could not be saved ({ex}). Nothing was changed.")

    def save(self) -> None:
        self._write(self.templates, self.tools, self.prompts)

    def _commit(self, templates: list | None = None, tools: list | None = None, prompts: list | None = None) -> None:
        """Transaction: the new state is written first; only when that worked does the in-memory state change (a failed save leaves memory = disk = the old state)."""
        t, c, p = (self.templates if templates is None else templates), (self.tools if tools is None else tools), (self.prompts if prompts is None else prompts)
        self._write(t, c, p)
        self.templates, self.tools, self.prompts = t, c, p

    # -- templates ----------------------------------------------------------------------------------------------
    def all_templates(self) -> list:
        return [SongTemplate(b["id"], b["name"], b["description"], b["structure"]) for b in BUILTIN_TEMPLATES] + self.templates

    def template_names(self, exclude_id: str = "") -> list:
        return [t.name for t in self.all_templates() if t.id != exclude_id]

    def save_template(self, tid: str | None, name: str, description: str, structure: str) -> SongTemplate:
        if tid and tid.startswith("builtin:"):
            raise PresetError("Built-in templates cannot be changed. Duplicate it to make your own copy.")
        n = check_name(name, self.template_names(tid or ""), "template")
        s = check_structure(structure)
        new = SongTemplate(tid if tid and any(t.id == tid for t in self.templates) else new_id("t"), n, (description or "").strip()[:200], s)
        lst = [new if t.id == new.id else t for t in self.templates]
        if not any(t.id == new.id for t in self.templates):
            lst.append(new)
        self._commit(templates=lst)
        return new

    def delete_template(self, tid: str) -> None:
        if tid.startswith("builtin:"):
            raise PresetError("Built-in templates cannot be deleted.")
        self._commit(templates=[t for t in self.templates if t.id != tid])

    def duplicate_template(self, tid: str) -> SongTemplate:
        src = next((t for t in self.all_templates() if t.id == tid), None)
        if src is None:
            raise PresetError("That template no longer exists.")
        return self.save_template(None, self._copy_name(src.name, self.template_names()), src.description, src.structure)

    # -- custom tools -------------------------------------------------------------------------------------------
    def save_tool(self, tid: str | None, name: str, type_: str, content: str = "", prefix: str = "", suffix: str = "", empty_pair: bool = False, description: str = "") -> CustomTool:
        n = check_name(name, [t.name for t in self.tools if t.id != tid], "tool")
        new = check_tool(CustomTool(tid or new_id("c"), n, type_, content, prefix, suffix, empty_pair, description))
        lst = [new if t.id == new.id else t for t in self.tools]
        if not any(t.id == new.id for t in self.tools):
            lst.append(new)
        self._commit(tools=lst)
        return new

    def delete_tool(self, tid: str) -> None:
        self._commit(tools=[t for t in self.tools if t.id != tid])

    def tool(self, key_or_id: str) -> "CustomTool | None":
        i = key_or_id[len(CUSTOM_PREFIX):] if key_or_id.startswith(CUSTOM_PREFIX) else key_or_id
        return next((t for t in self.tools if t.id == i), None)

    # -- prompts ------------------------------------------------------------------------------------------------
    def save_prompt(self, pid: str | None, name: str, description: str, prompt: str, scope: str) -> AiPrompt:
        n = check_name(name, [p.name for p in self.prompts if p.id != pid], "prompt")
        if scope not in SCOPES:
            raise PresetError("Choose a target scope.")
        new = AiPrompt(pid or new_id("p"), n, (description or "").strip()[:200], check_prompt(prompt), scope)
        lst = [new if p.id == new.id else p for p in self.prompts]
        if not any(p.id == new.id for p in self.prompts):
            lst.append(new)
        self._commit(prompts=lst)
        return new

    def delete_prompt(self, pid: str) -> None:
        self._commit(prompts=[p for p in self.prompts if p.id != pid])

    def duplicate_prompt(self, pid: str) -> AiPrompt:
        src = next((p for p in self.prompts if p.id == pid), None)
        if src is None:
            raise PresetError("That prompt no longer exists.")
        return self.save_prompt(None, self._copy_name(src.name, [p.name for p in self.prompts]), src.description, src.prompt, src.scope)

    @staticmethod
    def _copy_name(name: str, taken: list) -> str:
        t = {x.casefold() for x in taken}
        cand, k = f"{name} (copy)"[:NAME_MAX], 2
        while cand.casefold() in t:
            cand = f"{name} (copy {k})"[:NAME_MAX]
            k += 1
        return cand


def _loads_ok(kind: str, d: dict) -> bool:
    try:
        {"song_templates": template_from_dict, "custom_tools": tool_from_dict, "ai_prompts": prompt_from_dict}[kind](d)
        return True
    except Exception:
        return False


# ── applying (pure: text in, Edit out; the app puts the Edit through set_lyrics) ────────────────────────────────
def insert_template(text: str, pos: int, structure: str, mode: str) -> Edit:
    """mode: 'empty' (the song has no text), 'before_line' (above the line that holds `pos`) or 'append' (at the end). Nothing existing is replaced or removed, a blank line
    separates the template from the text around it, and the inserted block ends up selected."""
    block = structure.strip("\n")
    if mode == "empty":
        if text.strip():
            raise PresetError("The song already has text.")
        return Edit(block + "\n", len(block) + 1, len(block) + 1, "Inserted the template")
    if mode == "before_line":
        pos = max(0, min(pos, len(text)))
        ls = line_bounds(text, pos)[0]
        lead = "" if ls == 0 or text[:ls].endswith("\n\n") else "\n"
        ins = lead + block + "\n\n"
        return Edit(text[:ls] + ins + text[ls:], ls + len(lead), ls + len(lead) + len(block), "Inserted the template above the current line")
    if mode == "append":
        lead = "" if not text else ("" if text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n"))
        ins = lead + block + "\n"
        return Edit(text + ins, len(text) + len(lead), len(text) + len(lead) + len(block), "Added the template at the end of the song")
    raise PresetError("Unknown insert position.")


def apply_custom(tool: CustomTool, text: str, start: int, end: int) -> Edit:
    """What a custom tool does at the caret / selection. snippet: a single [tag] line goes where the built-in cue tools put theirs, any other text goes at the caret (after a
    selection). wrap: prefix + selection + suffix (again on a wrapped selection removes them); with no selection it inserts an empty pair only when the tool says so."""
    if tool.type == "snippet":
        c = tool.content.strip("\n")
        if "\n" not in c and TAG_LINE.match(c):
            e = _insert_cue(text, start, c.strip()[1:-1], tool.name)
            e.message = f"{tool.name}: inserted {c.strip()}"
            return e
        pos = end if end > start else start
        e = insert_text(text, pos, c, tool.name)
        return e
    if tool.type == "wrap":
        if start >= end:
            if not tool.empty_pair:
                raise ToolError(f"{tool.name}: select the text to wrap first (nothing is guessed).")
            new = text[:start] + tool.prefix + tool.suffix + text[start:]
            p = start + len(tool.prefix)
            return Edit(new, p, p, f"{tool.name}: type between {tool.prefix}{tool.suffix}")
        sel = text[start:end]
        if len(sel) >= len(tool.prefix) + len(tool.suffix) and sel.startswith(tool.prefix) and sel.endswith(tool.suffix):
            inner = sel[len(tool.prefix):len(sel) - len(tool.suffix)]
            return Edit(text[:start] + inner + text[end:], start, start + len(inner), f"{tool.name} removed")
        new = text[:start] + tool.prefix + sel + tool.suffix + text[end:]
        return Edit(new, start, end + len(tool.prefix) + len(tool.suffix), tool.name)
    raise ToolError("Unknown tool type")
