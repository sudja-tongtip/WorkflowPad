# -*- coding: utf-8 -*-
"""llm_overrides.py - one switch for the LLM values the program sets itself.

The ComfyUI workflow (LLMSessionChatNode) carries its own sampler / context values. Some stages replace a few of them with values the program was tested with (for example B uses
repeat_penalty 1.0 instead of the workflow's 1.12). This module is the single place where that happens:

    PROGRAM   (default)  the program's tested values are written into the workflow COPY of each call; behaviour is exactly what it was before this switch existed
    WORKFLOW             the program does not touch those values: every call uses the workflow file's own

Only the sampler / context values that a stage OVERRIDES go through apply(): the plumbing every call needs (user_text, system_prompt, session_id, reset_session, max_tokens, and the
conversation modes' max_turns / summarize_old_history) is never affected by the switch. Covered: repeat_penalty of Shot List and Page List, temperature / n_ctx of the Settings-driven
calls (Consistency Check, Visual Direction, Story Editor and the other run_llm users), the Project Assistant chat values, n_ctx of Image Prompts (single call and conversation) and of the
Story Bible conversation. Code that plans against the context (conversation budgets) reads effective_n_ctx(). Timeouts are waits of the program, not workflow values.
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
        if _mode == PROGRAM and value is not None:        # None = the program has no value of its own for this call: the workflow's stays
            inputs[name] = value
        used[name] = inputs.get(name)
    return used


def workflow_n_ctx(wf_template: dict | None) -> int:
    """The n_ctx the workflow file itself carries (0 when there is none or it cannot be read)."""
    for node in (wf_template or {}).values():
        if isinstance(node, dict) and node.get("class_type") == "LLMSessionChatNode":
            try:
                return max(0, int((node.get("inputs") or {}).get("n_ctx") or 0))
            except (TypeError, ValueError):
                return 0
    return 0


def effective_n_ctx(wf_template: dict | None, program_value: int) -> int:
    """The context size a call really runs with, for code that PLANS against it (conversation budgets): the program's value in PROGRAM mode; in WORKFLOW mode the workflow's own,
    falling back to the program's value when the workflow has none."""
    if _mode == WORKFLOW:
        wf_value = workflow_n_ctx(wf_template)
        if wf_value > 0:
            return wf_value
    return int(program_value)


def describe(used: dict) -> str:
    """One log line: the mode and the values this call really uses."""
    vals = ", ".join(f"{k}={v}" for k, v in used.items()) or "nothing overridden"
    return f"⚙ LLM settings [{LABELS[_mode]}]: {vals}"
