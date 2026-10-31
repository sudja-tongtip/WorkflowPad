"""A trace of what the specialists were asked and what came back, for finding out later WHY something happened (a proposal that did not arrive, a refused change, a report with a missing part).

One JSON object per line in %LOCALAPPDATA%\\WorkflowPad\\logs\\specialist_trace.jsonl (the file rotates at about 3 MB; one older file is kept). Every entry holds the request as it was sent (the lyrics
of that moment, the user's words), the raw answer of the model, and every step the program took with it (header read, lines dropped, proposal judged, the reason a proposal was not offered).
Nothing here is secret except the lyrics themselves, which stay on this computer. API keys are never written: nothing in this module receives one.
Writing the trace never raises and never slows a request noticeably: if the file cannot be written the entry is simply lost.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

MAX_BYTES = 3_000_000
VERSION = 1
ENABLED = True            # the user can switch the log off (specialist menu); nothing is written then


def trace_path() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "WorkflowPad" / "logs" / "specialist_trace.jsonl"


def log_event(kind: str, **data) -> None:
    """Append one entry. `kind`: chat | report | arrange | action. Long texts are kept whole up to 20,000 characters."""
    if not ENABLED:
        return
    try:
        p = trace_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > MAX_BYTES:
            old = p.with_name("specialist_trace.1.jsonl")
            try:
                old.unlink(missing_ok=True)
            except OSError:
                pass
            p.replace(old)
        entry = {"v": VERSION, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind}
        for k, v in data.items():
            if k.lower() in ("key", "api_key", "apikey", "token", "secret"):
                continue
            entry[k] = v[:20000] if isinstance(v, str) else v
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_last(n: int = 20, kind: str = "") -> list:
    """The last `n` entries (newest last), optionally only one kind. For tests and for looking things up."""
    out = []
    try:
        for line in trace_path().read_text(encoding="utf-8").splitlines()[-2000:]:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not kind or e.get("kind") == kind:
                out.append(e)
    except OSError:
        return []
    return out[-n:]
