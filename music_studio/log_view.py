"""Text for the View logs dialog. Reading only: nothing here writes a log, a session, or a song.

System logs are the files in %LOCALAPPDATA%\\WorkflowPad\\logs (the Music Studio log, the error log, the one-line AI call log).
LLM talk is the specialist trace in that same folder, and the local session files under the LLM history folder (one JSON file per local conversation).
A long file is cut to its newest part. A missing file is reported, not created.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

TEXT_TAIL = 100_000
TRACE_TAIL = 500_000
TRACE_KEEP = 40
FIELD_CAP = 8_000
TURN_CAP = 20_000
SESSION_LIST = 30

_FILES = {
    "system": "music_studio.log",
    "errors": "errors.log",
    "calls": "ai_calls.log",
    "trace": "specialist_trace.jsonl",
}
_HEAD = ("ts", "kind", "specialist", "backend", "model", "song_id", "sheet_id")
_BODY = ("instruction", "reply_shown", "raw_answer", "error", "steps", "proposal_offered", "failed",
         "original", "applied_text", "finding", "edit", "line_hint", "template", "facts")
_HIDE = {"lyrics", "selected", "request_tail", "lyrics_hash", "v", "key", "api_key", "apikey", "token", "secret"}
_BEARER = re.compile(r"(?i)(Bearer\s+)\S+")
_ASSIGNED_KEY = re.compile(r"(?i)((?:api[_-]?key|token|secret)[\"']?\s*[:=]\s*[\"'])[^\"'\s]{6,}")


@dataclass(frozen=True)
class LogText:
    path: str
    text: str
    notes: tuple


def logs_dir() -> Path:
    return Path(os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")) / "WorkflowPad" / "logs"


def history_dir() -> "Path | None":
    """The local LLM session folder from the shared chat config, when that folder exists."""
    try:
        from . import llm_chat as LC
        raw = str(LC.load_cfg().get("history_dir") or "").strip()
    except Exception:
        return None
    if not raw:
        return None
    folder = Path(raw)
    return folder if folder.is_dir() else None


def redact(text: str) -> str:
    """Hide a bearer token or an assigned key if one ever landed in a log line. Ordinary words stay."""
    text = _BEARER.sub(r"\1…", text or "")
    return _ASSIGNED_KEY.sub(r"\1…", text)


def _tail(path: Path, limit: int) -> tuple:
    """(text, truncated). The end of the file, starting at a line boundary when the cut lands in the middle of one."""
    size = path.stat().st_size
    with path.open("rb") as f:
        if size > limit:
            f.seek(size - limit)
            chunk = f.read(limit)
            cut = chunk.find(b"\n")
            if 0 <= cut < len(chunk) - 1:
                chunk = chunk[cut + 1:]
            truncated = True
        else:
            chunk = f.read()
            truncated = False
    return chunk.decode("utf-8", errors="replace"), truncated


def _older(path: Path) -> bool:
    parent, name = path.parent, path.name
    names = [name + ".1", name + ".2"]
    if name.endswith(".jsonl"):
        names.append(name[:-6] + ".1.jsonl")
    return any((parent / n).is_file() for n in names)


def _clip(text: str, cap: int) -> str:
    text = (text or "").replace("\r\n", "\n")
    if len(text) <= cap:
        return text
    return text[:cap] + "\n… truncated …"


def _plain(value, cap: int) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return _clip(" | ".join(str(x) for x in value), cap)
    if isinstance(value, dict):
        return _clip(json.dumps(value, ensure_ascii=False), cap)
    return _clip(str(value), cap)


def _format_trace(raw: str, truncated_file: bool) -> tuple:
    """(text, notes) for the newest specialist-trace entries. The lyric body is left out: the instruction, the reply and the raw answer are the conversation."""
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            rows.append(line)
            continue
        if not isinstance(entry, dict):
            rows.append(line)
            continue
        head = [str(entry[k]) for k in _HEAD if entry.get(k) not in (None, "")]
        blocks = ["========== " + " · ".join(head) + " =========="]
        reply = entry.get("reply_shown")
        for key in _BODY:
            if key not in entry or key in _HIDE:
                continue
            if key == "raw_answer" and reply not in (None, "") and entry.get(key) == reply:
                blocks.append("raw_answer: (same as the reply shown)")
                continue
            blocks.append(f"{key}:\n{_plain(entry[key], FIELD_CAP)}")
        for key in sorted(entry):
            if key in _HEAD or key in _BODY or key in _HIDE or key.lower() in _HIDE:
                continue
            blocks.append(f"{key}:\n{_plain(entry[key], FIELD_CAP)}")
        rows.append("\n".join(blocks))
    shown = rows[-TRACE_KEEP:]
    notes = []
    if truncated_file or len(rows) > TRACE_KEEP:
        notes.append("Showing the newest part of the specialist conversation.")
    return "\n\n".join(shown), tuple(notes)


def _format_session(path: Path) -> LogText:
    if not path.is_file():
        return LogText(str(path), "", ("This log file is not there yet.",))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        try:
            raw, cut = _tail(path, TEXT_TAIL)
        except OSError:
            return LogText(str(path), "", ("This session file could not be read as JSON.",))
        notes = ["This session file could not be read as JSON."]
        if cut:
            notes.append("Showing the end of a longer file.")
        return LogText(str(path), redact(raw), tuple(notes))
    if not isinstance(data, dict):
        return LogText(str(path), "", ("This session file could not be read as JSON.",))
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    sig = meta.get("model_signature") if isinstance(meta.get("model_signature"), dict) else {}
    model = Path(str(sig.get("model_file") or "")).name
    lines = [f"session {path.stem}", f"created {meta.get('created_at') or '-'}"]
    if model:
        lines.append(f"model {model}")
    system = str(data.get("system_prompt") or "").strip()
    if system:
        lines.append("system: " + system.splitlines()[0][:180])
    turns = data.get("turns") if isinstance(data.get("turns"), list) else []
    if not turns:
        lines.append("(no turns)")
    for turn in turns:
        if not isinstance(turn, dict):
            lines.append("----- turn -----\n" + _clip(str(turn), TURN_CAP))
            continue
        user = (turn.get("user") or {}).get("text") if isinstance(turn.get("user"), dict) else ""
        assistant = (turn.get("assistant") or {}).get("text") if isinstance(turn.get("assistant"), dict) else ""
        lines.append(f"----- turn {turn.get('id', '')} {turn.get('t') or ''} -----".rstrip())
        lines.append("USER\n" + (_clip(str(user or ""), TURN_CAP) or "(empty)"))
        lines.append("ASSISTANT\n" + (_clip(str(assistant or ""), TURN_CAP) or "(no assistant text)"))
    return LogText(str(path), redact("\n".join(lines)), ())


def list_sessions(directory: "Path | None" = None, limit: int = SESSION_LIST) -> tuple:
    """(newest (path, label) pairs, how many session files the folder has). `directory` None uses the configured history folder."""
    folder = history_dir() if directory is None else directory
    if folder is None or not Path(folder).is_dir():
        return [], 0
    found = []
    for path in Path(folder).glob("music_*.json"):
        try:
            found.append((path.stat().st_mtime, path))
        except OSError:
            continue
    found.sort(key=lambda item: item[0], reverse=True)
    items = []
    for _mtime, path in found[:limit]:
        label = path.stem
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            meta = data.get("meta") if isinstance(data, dict) else {}
            if isinstance(meta, dict):
                created = str(meta.get("created_at") or "")[:16].replace("T", " ")
                sig = meta.get("model_signature") if isinstance(meta.get("model_signature"), dict) else {}
                model = Path(str((sig or {}).get("model_file") or "")).name
                if len(model) > 42:
                    model = model[:39] + "..."
                label = " · ".join(bit for bit in (created, model, path.stem) if bit)
        except (OSError, ValueError):
            pass
        items.append((str(path), label))
    return items, len(found)


def _file_text(path: Path, kind: str) -> LogText:
    if not path.is_file():
        return LogText(str(path), "", ("This log file is not there yet.",))
    limit = TRACE_TAIL if kind == "trace" else TEXT_TAIL
    try:
        raw, cut = _tail(path, limit)
    except OSError:
        return LogText(str(path), "", ("This log file is not there yet.",))
    notes = []
    if kind == "trace":
        text, notes = _format_trace(raw, cut)
        notes = list(notes)
    else:
        text = raw
        if cut:
            notes.append("Showing the end of a longer file.")
    if not text.strip():
        return LogText(str(path), "", ("This log is empty.",))
    if _older(path):
        notes.append("Older copies are in the same folder.")
    return LogText(str(path), redact(text), tuple(notes))


def read(kind: str, session_path: str = "", *, logs: "Path | None" = None) -> LogText:
    """The text of one log. `kind` is system, errors, calls, trace, or sessions."""
    if kind == "sessions":
        if not session_path:
            return LogText("", "", ("No local LLM session was found.",))
        return _format_session(Path(session_path))
    name = _FILES.get(kind)
    if not name:
        return LogText("", "", ("This log file is not there yet.",))
    return _file_text((logs if logs is not None else logs_dir()) / name, kind)
