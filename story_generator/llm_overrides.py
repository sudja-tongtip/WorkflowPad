# -*- coding: utf-8 -*-
"""llm_overrides.py - one switch for the LLM values the program sets itself.

The ComfyUI workflow (LLMSessionChatNode) carries its own sampler / context values. Some stages replace a few of them with values the program was tested with (for example B uses
repeat_penalty 1.0 instead of the workflow's 1.12). This module is the single place where that happens:

    PROGRAM   (default)  the program's tested values are written into the workflow COPY of each call; behaviour is exactly what it was before this switch existed
    WORKFLOW             the program does not touch those values: every call uses the workflow file's own

Only the sampler / context values that a stage OVERRIDES go through apply(): the plumbing every call needs (user_text, system_prompt, session_id, reset_session, max_tokens) is never
affected by the switch. Phase 1 covers repeat_penalty of Shot List (B) and Page List (B Manga); the other stages are moved onto apply() one by one.
"""
from __future__ import annotations

PROGRAM = "program"
WORKFLOW = "workflow"
LABELS = {PROGRAM: "Program defaults", WORKFLOW: "Workflow file"}

_mode: str = PROGRAM


def set_mode(mode: str | None) -> str:
    """Set the global mode. Anything that is not 'workflow' means PROGRAM (the safe, tested default). Returns the mode now in force."""
    global _mode
    _mode = WORKFLOW if str(mode or "").strip().lower() == WORKFLOW else PROGRAM
    return _mode


def get_mode() -> str:
    return _mode


def is_program() -> bool:
    return _mode == PROGRAM


def apply(inputs: dict, **program_values) -> dict:
    """Write the program's values into `inputs` (the node inputs of a workflow COPY) in PROGRAM mode; leave them alone in WORKFLOW mode.
    Returns {name: value that the call really uses} so the caller can log it: the program's value, or what the workflow file already holds (None = not set in the workflow)."""
    used = {}
    for name, value in program_values.items():
        if _mode == PROGRAM:
            inputs[name] = value
        used[name] = inputs.get(name)
    return used


def describe(used: dict) -> str:
    """One log line: the mode and the values this call really uses."""
    vals = ", ".join(f"{k}={v}" for k, v in used.items()) or "nothing overridden"
    return f"⚙ LLM settings [{LABELS[_mode]}]: {vals}"
