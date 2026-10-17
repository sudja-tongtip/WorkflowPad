"""visual_direction.py — the ONE shared definition of visual-direction controls.

Visual Preset = a UI macro that resolves (in Python, no LLM) to seven controls. Fill, Fill Manga and
the Director all read their meanings from here, so a semantic changed once changes everywhere.

Every control authorizes HOW only (framing, performance, composition, camera, light, environment,
motion) — never WHAT (people, identity, objects, core action/contact, state, location, dialogue,
chronology, outcome). The presets themselves are never sent to an LLM; only the resolved control
values are.
"""

from __future__ import annotations

__version__ = "20260926_visual_direction_a1"

SHOT_SCALE_RULES = {
    "": "OPEN — no scale requirement; keep or choose the framing that best serves this moment.",
    "balanced": "BALANCED — mixed distances, action stays readable.",
    "epic": "EXPANSIVE — wide, the location dominates.",
    "intimate": "INTIMATE — closer, reactions dominate.",
    "chamber": "CHAMBER — close, compressed depth, faces and hands.",
}

EXPRESSION_RULES = {
    "natural": "NATURAL — subtle, restrained, realistic performance.",
    "expressive": "EXPRESSIVE — clearly readable, cinematic performance.",
    "heightened": "HEIGHTENED — intense, dramatic, strongly readable performance.",
}

COMPOSITION_RULES = {
    "off": "PRESERVE — keep the existing framing and arrangement; do not restage it.",
    "balanced": "BALANCED — make the visual organization clear, deliberate, and easy to read.",
    "cinematic": "CINEMATIC — create stronger visual hierarchy and depth.",
    "bold": "BOLD — create a strong, graphically confident composition.",
}

CAMERA_RULES = {
    "stable": "STABLE — the camera should feel level, composed, and restrained.",
    "natural": "NATURAL — choose the camera character that best serves this beat.",
    "tense": "TENSE — create clear visual pressure; choose the technique yourself.",
    "chaotic": "CHAOTIC — create unmistakable visual instability; choose the technique yourself.",
}

LIGHTING_RULES = {
    "scene": "FOLLOW SCENE — preserve the existing lighting direction/source treatment.",
    "enhance": "ENHANCE — enrich the existing light naturally; visual-only bounce, falloff, reflection, glow, or separation may be added when useful.",
    "dramatic": "DRAMATIC — create stronger cinematic light/shadow impact; you may introduce visual-only rim, bounce, shafts, glow, or shadow separation when useful, but never add a story prop just to justify the light.",
    "soft": "SOFT — reshape the light into broader, gentler, diffused illumination with soft falloff; do not change story time/place.",
}

ENVIRONMENT_RULES = {
    "off": "OFF — do not add environmental reaction beyond what the prompt already states.",
    "natural": "NATURAL — let the established environment act plausibly on hair, fabric, surfaces, atmosphere, reflections, and depth; small visual reactions may be added.",
    "emphasized": "EMPHASIZED — make the established environment visibly participate. You may add plausible micro-atmosphere and physical response (haze, drifting moisture/dust, hair/fabric response, wet reflection, etc.) when consistent with the place; do not invent a contradictory major weather event.",
}

MOTION_RULES = {
    "natural": "NATURAL — preserve the resolved pose/action phase.",
    "dynamic": "DYNAMIC — make the SAME action feel more energetic through timing and secondary motion. Do not change the pose, the body support, or the contact.",
    "peak": "PEAK MOMENT — choose the strongest readable instant of the SAME ongoing action; you may change its pose/phase, but never create a new action or advance the story.",
}

# closes every brief: direction is HOW the camera sees reality, never a license to reshape it
SPATIAL_INVARIANT = ("Visual direction changes how the camera sees reality; it never overrides "
                     "canonical spatial truth or physical object geometry.")

# key order = the order controls are listed in a brief
CONTROL_KEYS = ("shot_scale", "expression", "composition", "camera_energy", "lighting", "environment", "motion")

VISUAL_PRESETS: dict[str, dict[str, str]] = {
    "natural":   {"shot_scale": "",         "expression": "natural",    "composition": "balanced",  "camera_energy": "natural", "lighting": "enhance",  "environment": "natural",    "motion": "natural"},
    "cinematic": {"shot_scale": "",         "expression": "expressive", "composition": "cinematic", "camera_energy": "natural", "lighting": "dramatic", "environment": "natural",    "motion": "dynamic"},
    "epic":      {"shot_scale": "epic",     "expression": "expressive", "composition": "cinematic", "camera_energy": "stable",  "lighting": "dramatic", "environment": "emphasized", "motion": "dynamic"},
    "intimate":  {"shot_scale": "intimate", "expression": "natural",    "composition": "balanced",  "camera_energy": "natural", "lighting": "soft",     "environment": "natural",    "motion": "natural"},
    "action":    {"shot_scale": "balanced", "expression": "heightened", "composition": "bold",      "camera_energy": "chaotic", "lighting": "dramatic", "environment": "emphasized", "motion": "peak"},
}

# (key, UI label) — UI dropdown text for the presets
VISUAL_PRESET_OPTIONS = [
    ("natural",   "🌿 Natural — improve the frame, do not over-direct"),
    ("cinematic", "🎞 Cinematic — depth, staged light, readable motion"),
    ("epic",      "🏔 Epic — wide, the location dominates"),
    ("intimate",  "🔍 Intimate — closer, softer, reactions lead"),
    ("action",    "💥 Action — bold graphics, unstable camera, peak instants"),
]

DEFAULT_PRESET = "natural"

_RULES = {
    "shot_scale": SHOT_SCALE_RULES, "expression": EXPRESSION_RULES, "composition": COMPOSITION_RULES,
    "camera_energy": CAMERA_RULES, "lighting": LIGHTING_RULES, "environment": ENVIRONMENT_RULES,
    "motion": MOTION_RULES,
}
_LABELS = {
    "shot_scale": "Shot Scale", "expression": "Expression", "composition": "Composition",
    "camera_energy": "Camera Energy", "lighting": "Lighting", "environment": "Environment Response",
    "motion": "Motion",
}


def normalize_preset(key: str) -> str:
    key = (key or "").strip().lower()
    return key if key in VISUAL_PRESETS else DEFAULT_PRESET


def resolve_visual_preset(key: str) -> dict[str, str]:
    """Preset key -> the seven resolved control values (a copy). Unknown/empty -> Natural."""
    return dict(VISUAL_PRESETS[normalize_preset(key)])


def control_rule(control: str, value: str) -> str:
    """The shared semantic text for one control value (falls back to that control's default value)."""
    rules = _RULES[control]
    if value in rules:
        return rules[value]
    return rules.get(VISUAL_PRESETS[DEFAULT_PRESET][control], next(iter(rules.values())))


def build_visual_direction_brief(controls: dict[str, str], *, header: str = "VISUAL DIRECTION",
                                 include: tuple[str, ...] = CONTROL_KEYS,
                                 scale_line: str = "") -> str:
    """One concise block: `header` then one 'Label: semantic value' line per control. The preset name
    is never part of it. `scale_line` lets a caller with extra context (the Director's assigned
    distance) replace the Shot Scale text; every other line is the shared definition."""
    lines = [header]
    for key in include:
        text = scale_line if (key == "shot_scale" and scale_line) else control_rule(key, controls.get(key, ""))
        lines.append(f"{_LABELS[key]}: {text}")
    lines.append(SPATIAL_INVARIANT)
    return "\n".join(lines)


def visual_direction_for_preset(key: str) -> str:
    """Fill / Fill Manga entry point: preset key -> the VISUAL DIRECTION block."""
    return build_visual_direction_brief(resolve_visual_preset(key))
