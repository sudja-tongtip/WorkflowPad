"""
ui_helpers.py — Flet utility functions (stateless)
"""
__version__ = "20260726_1907"

import re

import flet as ft
from .constants import C, clean_overlay_text


def po(left=0, top=0, right=0, bottom=0):
    return ft.Padding(left=left, top=top, right=right, bottom=bottom)


def br(r):
    return ft.BorderRadius(r, r, r, r)


def divider(color=None):
    return ft.Divider(height=1, color=color or C["border"])


def _dlg_to_str(val) -> str:
    """normalize dialogue field (str | list | None) → str for display / overlay"""
    import json
    if val is None:
        return ""
    if isinstance(val, list):
        return json.dumps(val, ensure_ascii=False)
    return str(val)


# Text we may already have added to a prompt (current wording + the old one still found in older saved
# prompts) — so a prompt never gets its text elements injected twice. The wording deliberately avoids
# style words like "comic"/"manga": image models turn the WHOLE picture into a cartoon when they see them.
TEXT_OVERLAY_MARKERS = ("graphic text elements added over the picture", "comic book style manga")
_PIL_STYLES = ("narration_box_pil", "subtitle_bar")
_AI_STYLES = ("speech_bubble", "narration_box")


def _panel_text_instruction(panel: dict) -> str:
    """Krea2 text instruction for ONE manga panel, from that panel's CURRENT text fields."""
    narration = str(panel.get("narration") or "").strip()
    speech_char = str(panel.get("speech_char") or "").strip()
    speech_text = str(panel.get("subtitle") or panel.get("speech_text") or "").strip()
    parts = []
    if narration:
        parts.append(f'a small dark narration box in the upper left corner of this panel, sized to fit its text on a single line, '
                     f'with white text reading exactly "{narration}" and nothing else (no filler text, no extra lines)')
    if speech_text:
        who = f" pointing toward {speech_char}" if speech_char else ""
        parts.append(f'a white oval speech bubble{who} inside this panel containing exactly the words "{speech_text}" and nothing else')
    if not parts:
        return ""
    return ("Text inside this panel only: " + "; ".join(parts) +
            ". No other lettering in this panel, no words move between elements, and all text is sharp, legible, clean printed font and stays inside this panel.")


def inject_manga_panel_text(prompt: str, spec: dict) -> str:
    """Add each panel's own text instruction to THAT panel's PANEL n: section of the runtime prompt.
    Never page-level: a panel with no text gets nothing, and a section that cannot be found is skipped."""
    panels = spec.get("panels") or []
    headers = list(re.finditer(r"(?im)^[ \t]*PANEL[ \t]+(\d+)[ \t]*:", prompt))
    if not headers or not panels:
        return prompt
    style_at = re.search(r"(?m)^STYLE:", prompt)
    inserts = []
    for i, h in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(prompt)
        if style_at and style_at.start() > h.end():
            end = min(end, style_at.start())
        panel = next((p for p in panels if isinstance(p, dict) and p.get("panel") == int(h.group(1))), None)
        instr = _panel_text_instruction(panel) if panel else ""
        if instr:
            inserts.append((h.end() + len(prompt[h.end():end].rstrip()), " " + instr))
    for pos, text in sorted(inserts, reverse=True):
        prompt = prompt[:pos] + text + prompt[pos:]
    return prompt


def runtime_dialogue_prompt(prompt: str, shot: dict, style: str) -> str:
    """TEMPORARY runtime prompt for one render (never written back to shot["prompt"]).

    `style` is the CURRENT Dialogue Style dropdown value; the text is the shot's CURRENT
    narration/speech_char/subtitle, both read at render time so user edits and dropdown changes are
    respected. AI styles send a narration-box instruction when narration exists and a speech
    instruction when subtitle exists (both when both). PIL styles send nothing (code draws after the
    image). Manga never uses PIL: any style except none has Krea2 draw each panel's own text."""
    if not prompt or (style or "none") == "none":
        return prompt
    spec = shot.get("_manga_page_spec") if isinstance(shot, dict) else None
    if isinstance(spec, dict) and spec:
        injected = inject_manga_panel_text(prompt, spec)
        return _strip_section_labels(injected) if injected != prompt else prompt
    # Non-manga narration/subtitle is always drawn by PIL after the image (see pil_overlay_applies),
    # so nothing is injected into the prompt for it, whatever Dialogue Style is selected.
    return prompt


def draws_text_with_krea(shot: dict, style: str) -> bool:
    """True when the CURRENT Dialogue Style makes Krea2 itself draw text: the AI styles (speech_bubble,
    narration_box), or any non-none style on a Manga page (Manga text is always drawn by Krea2)."""
    style = style or "none"
    if isinstance(shot, dict) and shot.get("_manga_page_spec"):
        return style != "none"
    return False   # non-manga text is always PIL-drawn, never by Krea2


def assemble_runtime_prompt(prompt: str, shot: dict, style: str, style_kw: str,
                            append_scene_desc: bool, sheets_fallback: str = "") -> str:
    """The prompt text that goes to the image model for one render (temporary; never stored).

    Normal case: unchanged — optional [Story] block from shot_desc, [Style], [Detail].
    While Krea2 is drawing text: the labelled 8-block shot_desc ("(Beat: …) (Interaction: …)") and the
    bracket headings are NOT sent — the image model paints such words into the picture (it once drew a
    fake narration box starting with "Interaction…"). Only clean prose may add context: shot["story"]
    (nothing if it is empty), then the style keywords, then the Fill prompt.
    The [Story] block is the shot's story prose followed by its shot_desc blocks. When the shot has no prompt of
    its own, sheets_fallback (character / location / props sheets) is used as the [Detail] text instead, and the
    [Story] block is sent even when append_scene_desc is off (otherwise the shot's story would not reach the model)."""
    shot = shot if isinstance(shot, dict) else {}
    if not (prompt or "").strip():                       # no Fill prompt: the shot's own story/shot_desc must still reach the image model
        append_scene_desc = True
        if (sheets_fallback or "").strip():
            prompt = sheets_fallback.strip()
    if draws_text_with_krea(shot, style):
        parts = []
        story = (shot.get("story") or "").strip() if append_scene_desc else ""
        if story:
            parts.append(story)
        if style_kw:
            parts.append(style_kw)
        parts.append(prompt)
        return "\n\n".join(parts)
    desc = ""
    if append_scene_desc and shot:
        desc = (shot.get("shot_desc") or shot.get("scene_desc", "")).strip()
        story = (shot.get("story") or "").strip()
        if story:                                            # story prose first, then the 8-block description
            desc = f"{story}\n{desc}" if desc else story
    parts = []
    if desc:
        parts.append(f"[Story]\n{desc}")
    if style_kw:
        parts.append(f"[Style]\n{style_kw}")
    parts.append(f"[Detail]\n{prompt}")
    return "\n\n".join(parts)


# Headings of Fill's output blocks. They are structure for people, but when Krea2 is asked to DRAW
# text it happily paints those words into the picture ("Lock", "Location"...). So while text is being
# drawn by the image model they are removed from the runtime prompt — the block CONTENT stays.
_SECTION_LABEL_RE = re.compile(
    r"(?im)^[ \t]*(?:MAIN IMAGE DESCRIPTION|PAGE LAYOUT|FRAME LAYOUT|CAMERA|LIGHT|IDENTITY LOCK|LOCATION LOCK|OBJECT LOCK)[ \t]*:[ \t]*\n?")
_STYLE_LABEL_RE = re.compile(r"(?m)^[ \t]*STYLE[ \t]*:[ \t]*")


def _strip_section_labels(prompt: str) -> str:
    text = _STYLE_LABEL_RE.sub("", _SECTION_LABEL_RE.sub("", prompt))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


_BLOCK_HEADER_RE = re.compile(
    r"(?m)^(MAIN IMAGE DESCRIPTION|PAGE LAYOUT|PANEL \d+|FRAME LAYOUT|CAMERA|LIGHT|IDENTITY LOCK|LOCATION LOCK|OBJECT LOCK|STYLE):[ \t]*$")


def block_prompt_format_rule(shots) -> str:
    """Format description for assistants that read or rewrite shot prompts. Fill writes labelled blocks; a chat
    that is only told the legacy flat-paragraph format calls them wrong. Returns "" when no shot prompt carries
    block labels (structural header match only), so legacy projects keep the old rule."""
    seen = []
    for sh in shots or []:
        if isinstance(sh, dict):
            for h in _BLOCK_HEADER_RE.findall(str(sh.get("prompt") or "")):
                if h not in seen:
                    seen.append(h)
    if not seen:
        return ""
    return (
        "This project's shot prompts are labelled blocks written by the Render Prompt stage. The labelled structure IS "
        "the correct, intended format - never call it wrong or ask for a single paragraph. Each label sits on its own "
        "line, followed by its text, blocks separated by a blank line. Labels found in this project: " + ", ".join(seen) + ".\n"
        "Block roles: MAIN IMAGE DESCRIPTION = what is visibly happening (no camera or lighting); FRAME LAYOUT = screen-space "
        "placement of visible people and objects; CAMERA = POV, angle, framing, scale only; LIGHT = the established light; "
        "IDENTITY LOCK = who each visible person is (permanent traits and current clothing); LOCATION LOCK = the place and the "
        "subject's placement in it; OBJECT LOCK = current objects (only when the shot has objects); PAGE LAYOUT / PANEL n / STYLE "
        "appear in Manga pages.\n"
        "People who have a trigger in the character sheet are named by that trigger token in every block, not by their name.\n"
        "When you rewrite a prompt, keep every label in the same order, change only the blocks the request affects, copy the "
        "rest unchanged, and write the line breaks as \\n inside the JSON string."
    )


def pil_overlay_applies(shot) -> bool:
    """Non-manga shots draw narration/subtitle with PIL whenever the text exists; Manga never uses PIL."""
    return not (isinstance(shot, dict) and shot.get("_manga_page_spec"))


def _pil_overlay_texts(shot: dict, style: str) -> tuple:
    """TEXT-CHANNEL-A1: independent narration/subtitle text for PIL overlay. Field presence decides
    render intent, not dialogue_style exclusivity — for either PIL-capable style, BOTH channels
    render whenever populated (narration_box_pil no longer drops a manually-set speech_text, and
    subtitle_bar no longer drops a manually-set narration). Returns (narration_text, subtitle_text)."""
    # Non-manga: PIL always draws narration/subtitle when the text exists, whatever Dialogue Style is selected.
    if isinstance(shot, dict) and shot.get("_manga_page_spec"):
        return "", ""   # Manga never uses PIL — its text is drawn by Krea2 inside each panel
    narration = clean_overlay_text(shot.get("narration"))
    # NATIVE-SUBTITLE-READ-A1: Normal vNext canonical dialogue field is "subtitle"; old
    # pre-migration saved projects still carry "speech_text" instead. Native-first fallback so
    # PIL overlay doesn't drop dialogue for new shots while old-project compatibility stays intact.
    speech_text = clean_overlay_text(shot.get("subtitle") or shot.get("speech_text"))
    speech_char = clean_overlay_text(shot.get("speech_char"))
    subtitle = f"{speech_char}: {speech_text}" if (speech_text and speech_char) else speech_text
    return narration, subtitle


def _pil_overlay_text(shot: dict, style: str) -> str:
    """Back-compat single-string accessor. Prefer _pil_overlay_texts + apply_dual_text_overlay for
    real dual-channel rendering; this returns just one string (narration takes priority) for any
    caller not yet updated to the dual-channel call."""
    narration, subtitle = _pil_overlay_texts(shot, style)
    return narration or subtitle


def section(title: str, icon, *controls):
    return ft.Container(
        content=ft.Column([
            ft.Row([ft.Icon(icon, size=14, color=C["text3"]),
                    ft.Text(title, size=11, weight=ft.FontWeight.W_600, color=C["text2"])],
                   spacing=6),
            *controls,
        ], spacing=6),
        padding=po(left=14, right=14, top=10, bottom=12),
        border=ft.Border(bottom=ft.BorderSide(1, C["border"])),
    )


def collapsible_section(title: str, icon, *controls, expanded=False):
    return ft.Container(
        content=ft.ExpansionTile(
            title=ft.Row([
                ft.Icon(icon, size=14, color=C["text3"]),
                ft.Text(title, size=11, weight=ft.FontWeight.W_600, color=C["text2"]),
            ], spacing=6),
            expanded=expanded,
            tile_padding=po(left=10, right=6, top=4, bottom=4),
            controls_padding=po(left=14, right=14, top=4, bottom=10),
            controls=list(controls),
            text_color=C["text2"],
            icon_color=C["text3"],
            collapsed_text_color=C["text2"],
            collapsed_icon_color=C["text3"],
        ),
        border=ft.Border(bottom=ft.BorderSide(1, C["border"])),
    )


def group_header(title: str):
    return ft.Container(
        content=ft.Text(title, size=10, weight=ft.FontWeight.W_700, color=C["text3"]),
        padding=po(left=14, top=10, right=14, bottom=6),
        bgcolor=C["border"],
    )


def int_field(val, w=60):
    return ft.TextField(
        value=str(val), keyboard_type=ft.KeyboardType.NUMBER,
        text_size=12, height=36, width=w,
        border_color=C["border"], focused_border_color=C["accent"],
        bgcolor=C["surface"],
        content_padding=po(left=8, right=8, top=4, bottom=4),
    )
