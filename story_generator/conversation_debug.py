"""
conversation_debug.py - saves the LLM conversations of the multi-call engines (A1 Static / A1 Novel conversation, Fill conversation) to the debug folder.

Active only while the app's "LLM Debug Logging" switch is ON (the same switch as the per-call debug files). One FILE PER RUN: every session of that run (Fill opens
several) is in the same file, and the file is rewritten after every call, so a run that is stopped or crashes still leaves everything up to the last call.
Never raises: a write failure is reported once through on_log by the caller and the run goes on.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from .constants import DEBUG_DIR


def debug_on() -> bool:
    try:
        from . import llm_engine_tools as _T
        return bool(_T._llm_debug_on())
    except Exception:
        return False


def _debug_dir() -> Path:
    try:
        from . import llm_engine_tools as _T
        return Path(_T._llm_debug_dir or DEBUG_DIR)
    except Exception:
        return Path(DEBUG_DIR)


class ConversationDebugLog:
    """One run -> one file `conversation_<kind>_<timestamp>_<id>.json` in the debug folder (created on the first write)."""

    def __init__(self, kind: str):
        self.kind = kind
        self.enabled = debug_on()
        self.path: Path | None = None
        self.error = ""
        self._started = datetime.now()
        self._id = uuid.uuid4().hex[:6]

    def write(self, payload: dict) -> str | None:
        if not self.enabled:
            return None
        try:
            if self.path is None:
                d = _debug_dir()
                d.mkdir(parents=True, exist_ok=True)
                self.path = d / f"conversation_{self.kind}_{self._started.strftime('%Y%m%d_%H%M%S')}_{self._id}.json"
            doc = {"kind": self.kind, "started": self._started.isoformat(timespec="seconds"), "updated": datetime.now().isoformat(timespec="seconds"), **payload}
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
            os.replace(tmp, self.path)
            return str(self.path)
        except Exception as exc:                      # a debug file must never stop a run
            self.error = f"{type(exc).__name__}: {exc}"
            self.enabled = False
            return None
