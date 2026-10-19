"""
constants.py — Config, palette, style map, size map
"""
__version__ = "20260726_1907"

import os, re, shutil, sys, tempfile
from pathlib import Path

# ── App metadata (single source of truth for Story Generator) ──────────
APP_ID      = "story_generator"
APP_NAME    = "Story Generator"
APP_VERSION = "0.1.7"   # SemVer; see RELEASE.md

# ── Config ────────────────────────────────────────────────────────────
# App root = the folder that holds comfy_apis/, icons/, stories/ ... Source run: the project folder. Frozen
# (PyInstaller) run: the folder next to the EXE — NOT sys._MEIPASS, which is read-only bundle internals, and
# not Path(__file__), which then points inside the bundle.
APP_ROOT     = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
COMFY_HOST   = "127.0.0.1:8188"
API_DIR      = APP_ROOT / "comfy_apis" / "image_create"  # text-to-image workflows (Export (API) from ComfyUI)
IMAGE_EDIT_DIR = APP_ROOT / "comfy_apis" / "image_edit"  # workflows that need a source image: img2img / inpaint / identity pass
LLM_DIR      = APP_ROOT / "comfy_apis" / "llm"
CAPTION_DIR  = APP_ROOT / "comfy_apis" / "caption"    # image -> prompt workflows (JoyCaption, Florence2)
# ── User data lives in the user's profile, never next to the program (the program folder may be read-only, and an update must not touch it) ──
USER_CONFIG_DIR = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "WorkflowPad"          # stories + last session
USER_LOCAL_DIR  = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "WorkflowPad"       # big / disposable data
DEBUG_DIR    = USER_LOCAL_DIR / "debug"
SAVE_PATH    = USER_CONFIG_DIR / "saved_story.json"
STORIES_DIR  = USER_CONFIG_DIR / "stories"
for _d in (USER_CONFIG_DIR, STORIES_DIR):
    try: _d.mkdir(parents=True, exist_ok=True)
    except OSError: pass


def _copy_once(old: Path, new: Path) -> None:
    """One-time copy of a file an earlier build kept next to the program. Never overwrites, never deletes the old copy."""
    try:
        if old.is_file() and not new.exists():
            new.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, new)
    except OSError:
        pass


def migrate_user_data() -> list[str]:
    """Copy the stories and the last session of an earlier build (kept next to the program) into the user profile, once.
    A marker file stops it from running again, so a story the user deletes later never comes back. Returns what was copied."""
    done = []
    marker = STORIES_DIR / ".migrated"
    try:
        _copy_once(APP_ROOT / "saved_story.json", SAVE_PATH)
        if not marker.exists():
            old = APP_ROOT / "stories"
            if old.is_dir() and old.resolve() != STORIES_DIR.resolve():
                for p in sorted(old.glob("*.json")):
                    if not (STORIES_DIR / p.name).exists():
                        _copy_once(p, STORIES_DIR / p.name)
                        done.append(p.name)
            marker.write_text("stories copied from the program folder once; delete this file only to copy again\n", encoding="utf-8")
    except OSError:
        pass
    return done


migrate_user_data()
TEMP_DIR     = Path(tempfile.gettempdir()) / "story_gen_temp"
TEMP_DIR.mkdir(exist_ok=True)

# ── Pipeline stage terminology ────────────────────────────────────────
# Canonical production-language names for the pipeline stages. Legacy shorthand that still appears in
# module/file names, serialized project keys and machine identifiers (kept for compatibility):
#   Groom = Story Editor          A1 = Story Bible     A2 = Beat Sheet
#   B     = Shot List          Fill = Image Prompts (Normal / Manga)
#   Checker = Consistency Check    Director = Visual Direction
# Pipeline: Story Editor -> Story Bible -> Beat Sheet -> Shot List
#           -> Image Prompts (Normal / Manga) -> Consistency Check -> Visual Direction
STAGE_STORY_EDITING        = "Story Editor"
STAGE_PRODUCTION_REFERENCE = "Story Bible"
STAGE_BEAT_DECOMPOSITION   = "Beat Sheet"
STAGE_SHOT_PLANNING        = "Shot List"
STAGE_RENDER_PROMPT        = "Image Prompt"
STAGE_NORMAL_RENDER_PROMPT = "Image Prompts"
STAGE_MANGA_RENDER_PROMPT  = "Manga Prompts"
STAGE_CONSISTENCY_CHECK    = "Consistency Check"
STAGE_VISUAL_DIRECTION     = "Visual Direction"

# ── Credentials (AppData — outside the project folder) ──────────────────
_appdata = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
CREDS_DIR  = _appdata / "StoryGenerator"
CREDS_DIR.mkdir(parents=True, exist_ok=True)
CREDS_PATH = CREDS_DIR / "credentials.json"

# ── Palette ───────────────────────────────────────────────────────────
C = {
    "bg"        : "#F0F0F0",
    "surface"   : "#FFFFFF",
    "panel"     : "#F7F7F8",
    "border"    : "#E2E2E6",
    "border2"   : "#D0D0D6",
    "accent"    : "#2563EB",
    "accent_lt" : "#EEF3FE",
    "green"     : "#16A34A",
    "green_lt"  : "#EDFAF3",
    "red"       : "#DC2626",
    "red_lt"    : "#FEF2F2",
    "text1"     : "#111111",
    "text2"     : "#555560",
    "text3"     : "#9999A8",
    "amber"     : "#D97706",
    "purple"    : "#7C3AED",
    "purple_lt" : "#F5F3FF",
}

# ── Image Style Map ───────────────────────────────────────────────────
STYLE_MAP = {
    "none"         : ("🚫 No style specified",   ""),
    "realistic"    : ("📷 Realistic",       "photorealistic, a photograph of, cinematic film grain, sharp focus"),
    "anime"        : ("🎌 Anime",           "anime style, cel shaded, clean line art, vibrant saturated colors, 2D illustration, manga influence"),
    "manga"        : ("📖 Manga",           "manga style, black and white, bold ink lines, high contrast"),
    "manga_shonen" : ("⚡ Manga Shonen",     "shonen manga style, bold thick linework, dynamic action lines, speed lines, high contrast screen tone"),
    "manga_shojo"  : ("🌸 Manga Shojo",      "shojo manga style, delicate thin linework, soft floral screen tone, romantic dreamy atmosphere"),
    "manga_seinen" : ("🗡️ Manga Seinen",     "seinen manga style, realistic detailed linework, gritty cross-hatch shading, dark cinematic tone"),
    "manga_josei"  : ("💐 Manga Josei",      "josei manga style, soft naturalistic linework, muted screen tone shading"),
    "manga_hentai" : ("🔞 Manga Hentai",     "hentai manga style, doujin art style, clean bold linework, glossy screen tone highlights"),
    "comic"        : ("💥 Comic Book",      "american comic book style, bold outlines, halftone dots, flat colors, dynamic poses"),
    "fantasy_art"  : ("🎨 Fantasy Art",     "fantasy concept art, painterly style, detailed illustration, artstation quality, epic atmosphere"),
    "oil_painting" : ("🖼️ Oil Painting",    "oil painting, classical art style, rich colors, museum quality"),
    "watercolor"   : ("💧 Watercolor",      "watercolor painting, transparent washes"),
    "3d_render"    : ("💻 3D Render",       "3D render, octane render, ray tracing"),
    "pixel_art"    : ("👾 Pixel Art",       "pixel art, 16-bit retro style, limited color palette, crisp pixels, retro game aesthetic"),
    "ghibli"       : ("🌿 Ghibli Style",    "studio ghibli inspired, soft watercolor backgrounds, warm natural palette, hand drawn animation style"),
    "dark_fantasy" : ("🌑 Dark Fantasy",    "dark fantasy art, moody atmospheric lighting, gothic aesthetic, dramatic shadows, detailed illustration"),
    "cyberpunk"    : ("🌆 Cyberpunk",       "cyberpunk style, neon lights, rain-soaked streets, futuristic cityscape, high contrast, synthwave aesthetic"),
    "ink_sketch"   : ("✒️ Ink Sketch",      "ink sketch, monochrome pen drawing, expressive linework"),
    # SOURCE-STYLE-A1: no static keyword text here — the per-story source_style value is resolved
    # at Fill time (image_style == "as_source" → effective_style = source_style or ""), never from
    # this map's second element, which stays "" exactly like "none".
    "as_source"    : ("🎬 As Source",       ""),
}

# ── Style detail by shot scale ────────────────────────────────────────
# คำที่บรรยาย "พื้นผิว" (รูขุมขน ตำหนิผิว เนื้อสี รอยแปรง เส้นแรเงา) บังคับให้ image model
# ซูมเข้าหาสิ่งนั้น เพราะมันมองเห็นได้เฉพาะระยะใกล้ ถ้าใส่ไว้ในทุกช็อตจะได้ close-up ทั้งเรื่อง
# แม้ prompt จะสั่ง wide → แยกออกมาเติมเฉพาะระยะที่มองเห็นจริง
# ฝั่ง wide/extreme wide เติมคำ "สภาพแวดล้อม" แทน เพื่อดันไปทางกว้างแทนที่จะแค่ตัดคำออกเฉยๆ
# (แบบเดียวกับ cyberpunk/ghibli ที่ไม่เคยมีปัญหานี้ เพราะบรรยายฉาก ไม่ใช่ผิวคน)
STYLE_DETAIL_BY_SCALE: dict[str, dict[str, str]] = {
    "realistic": {
        "close-up":     "natural skin pores, skin imperfections, subsurface scattering",
        "medium":       "natural skin texture, subsurface scattering",
        "full body":    "",
        "wide":         "atmospheric depth, natural haze in the distance",
        "extreme wide": "vast atmospheric perspective, distant haze, deep landscape layers",
    },
    "manga": {
        "close-up":     "screen tone shading",
        "medium":       "screen tone shading",
        "full body":    "",
        "wide":         "bold black spotting, wide panel composition",
        "extreme wide": "bold black spotting, wide panel composition",
    },
    "manga_shonen": {
        "close-up":     "intense expressive eyes",
        "medium":       "intense expressive eyes",
        "full body":    "energetic dramatic poses",
        "wide":         "sweeping speed lines across the scene",
        "extreme wide": "sweeping speed lines across the scene",
    },
    "manga_shojo": {
        "close-up":     "large sparkling eyes, flowing hair detail, gentle blush shading",
        "medium":       "large sparkling eyes, gentle blush shading",
        "full body":    "flowing hair detail",
        "wide":         "soft floral background tones",
        "extreme wide": "delicate airy background, wide open composition",
    },
    "manga_seinen": {
        "close-up":     "fine detailed anatomy, restrained expressions",
        "medium":       "restrained expressions",
        "full body":    "mature proportions",
        "wide":         "gritty environmental detail",
        "extreme wide": "gritty environmental detail, wide desolate framing",
    },
    "manga_josei": {
        "close-up":     "subtle emotional expressions",
        "medium":       "subtle emotional expressions",
        "full body":    "realistic adult proportions",
        "wide":         "understated elegant composition",
        "extreme wide": "understated elegant composition, generous negative space",
    },
    "manga_hentai": {
        "close-up":     "vibrant expressive faces, exaggerated expressive anatomy",
        "medium":       "vibrant expressive faces",
        "full body":    "exaggerated expressive anatomy",
        "wide":         "",
        "extreme wide": "",
    },
    "oil_painting": {
        "close-up":     "impasto texture, visible brushwork",
        "medium":       "visible brushwork",
        "full body":    "",
        "wide":         "broad tonal masses, atmospheric depth",
        "extreme wide": "sweeping landscape composition, atmospheric perspective",
    },
    "watercolor": {
        "close-up":     "paper texture, soft wet edges, loose brushwork",
        "medium":       "soft wet edges, loose brushwork",
        "full body":    "soft wet edges",
        "wide":         "broad transparent washes, open white space",
        "extreme wide": "wide flowing washes, distant pale horizon",
    },
    "3d_render": {
        "close-up":     "subsurface scattering, detailed material microsurface",
        "medium":       "subsurface scattering",
        "full body":    "physically based materials",
        "wide":         "volumetric atmosphere, environment lighting",
        "extreme wide": "volumetric fog, vast environment lighting, deep draw distance",
    },
    "ink_sketch": {
        "close-up":     "cross hatching",
        "medium":       "cross hatching",
        "full body":    "loose gestural lines",
        "wide":         "sparse economical linework, open white space",
        "extreme wide": "sparse economical linework, open white space",
    },
}


def style_string_for_scale(style_key: str, shot_scale: str = "medium") -> str:
    """สตริง style ที่เหมาะกับระยะภาพของช็อตนั้น — ฐานจาก STYLE_MAP บวกรายละเอียด
    เฉพาะระยะที่มองเห็นได้จริง สไตล์ที่ไม่มีใน STYLE_DETAIL_BY_SCALE (anime, comic,
    fantasy_art, pixel_art, dark_fantasy, cyberpunk, ghibli, none) คืนค่าฐานตามเดิม"""
    base = STYLE_MAP.get(style_key, ("", ""))[1]
    detail = STYLE_DETAIL_BY_SCALE.get(style_key, {}).get(shot_scale, "")
    if base and detail:
        return f"{base}, {detail}"
    return base or detail


# ── Dialogue Style ────────────────────────────────────────────────────
DIALOGUE_STYLE_MAP = {
    "none":             "🚫 None (no dialogue drawn)",
    "speech_bubble":    "💬 Speech Bubble — AI drawn (≤8 words)",
    "narration_box":    "🎨 Narration Box — AI multi-box",
    "narration_box_pil":"📦 Narration Box — PIL single box (≤30 words)",
    "subtitle_bar":     "🎬 Subtitle Bar — PIL drawn (≤20 words)",
}

# Dialogue Style is also an AUTHORING hint for B: it decides which text fields B fills and their length.
# These limits bind B only — user-edited text is rendered exactly as written, never truncated later.
_DIALOGUE_AUTHORING = {
    "none": "narration, speech_char and subtitle all stay empty.",
    "speech_bubble": "spoken dialogue goes in subtitle (at most 8 words) and its speaker in speech_char; narration normally stays empty.",
    "narration_box": "the narration/caption goes in narration (at most 30 words); genuine spoken dialogue may also go in subtitle (at most 8 words); speech_char names the speaker when known.",
    "narration_box_pil": "the narration goes in narration (at most 30 words); genuine dialogue present in the source may stay in subtitle — do not erase it because the text is drawn by code; speech_char names the speaker when known.",
    "subtitle_bar": "spoken dialogue goes in subtitle (at most 20 words) and its speaker in speech_char; narration normally stays empty.",
}
_NARRATION_TYPE_HINT = {
    "caption": "Narration wording: a visual caption — who/what/where.",
    "bridge": "Narration wording: a literary bridge — feeling/stakes/cost.",
}


def dialogue_authoring_text(style: str, dialogue_lang: str = "en", narration_type: str = "caption",
                            unit: str = "shot") -> str:
    """One prompt line telling B (Normal or Manga) which text fields to author. "" for unknown style."""
    rule = _DIALOGUE_AUTHORING.get((style or "").strip())
    if not rule:
        return ""
    line = f"Dialogue style ({style}), per {unit}: {rule}"
    if style != "none":
        line += f" Write narration and subtitle text in {lang_prompt_name(dialogue_lang or 'en')}."
        if style in ("narration_box", "narration_box_pil"):
            line += " " + _NARRATION_TYPE_HINT.get(narration_type or "caption", _NARRATION_TYPE_HINT["caption"])
    if unit == "panel":
        line += " Put each panel's text in that panel's own tags."
    return line + " Do not invent dialogue or new story facts."

# Which B text channels each Dialogue Style may carry: (narration, subtitle/speech). Structural gate only.
_DIALOGUE_CHANNELS = {
    "none": (False, False),
    "speech_bubble": (False, True),
    "narration_box": (True, True),
    "narration_box_pil": (True, False),
    "subtitle_bar": (False, True),
}


def gate_dialogue_fields(item: dict, style: str) -> dict:
    """Blank the text channels the selected Dialogue Style does not use, on one B output shot/panel dict
    (in place). Runs right after B returns — never on later user edits. An unknown or empty style leaves
    the item untouched; speech_char goes with its subtitle."""
    chan = _DIALOGUE_CHANNELS.get((style or "").strip())
    if chan is None or not isinstance(item, dict):
        return item
    keep_narr, keep_sub = chan
    if not keep_narr:
        item["narration"] = ""
    if not keep_sub:
        item["subtitle"] = ""
        item["speech_char"] = ""
    return item


_INVISIBLE_RE = re.compile("[​‌‍⁠﻿ 　]")


def clean_overlay_text(value) -> str:
    """Text worth drawing, or "". Whitespace (incl. zero-width/no-break), empty list/None and
    punctuation-only strings count as blank, so a PIL box or bar is never drawn empty."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value if v is not None)
    text = _INVISIBLE_RE.sub(" ", str(value)).strip()
    return text if any(ch.isalnum() for ch in text) else ""

# UI label, prompt name (what we tell the LLM)
LANG_MAP: dict[str, tuple[str, str]] = {
    "en":    ("English", "English"),
    "th":    ("Thai", "Thai"),
    "zh":    ("Chinese (Simplified)", "Simplified Chinese"),
    "zh-tw": ("Chinese (Traditional)", "Traditional Chinese"),
    "ja":    ("Japanese", "Japanese"),
    "ko":    ("Korean", "Korean"),
    "vi":    ("Vietnamese", "Vietnamese"),
    "id":    ("Indonesian", "Indonesian"),
    "ms":    ("Malay", "Malay"),
    "hi":    ("Hindi", "Hindi"),
    "es":    ("Spanish", "Spanish"),
    "fr":    ("French", "French"),
    "de":    ("German", "German"),
    "pt":    ("Portuguese", "Portuguese"),
    "it":    ("Italian", "Italian"),
    "ru":    ("Russian", "Russian"),
    "ar":    ("Arabic", "Arabic"),
    "tr":    ("Turkish", "Turkish"),
    "nl":    ("Dutch", "Dutch"),
    "pl":    ("Polish", "Polish"),
}

def lang_prompt_name(code: str) -> str:
    """English name of a language for LLM prompts."""
    rec = LANG_MAP.get((code or "").strip()) or LANG_MAP["en"]
    return rec[1]

DIALOGUE_LANG_MAP = {k: v[0] for k, v in LANG_MAP.items()}

# (key, label, tone_hint, structure_hint)
GENRE_MAP: dict[str, tuple[str, str, str]] = {
    "none": (
        "🚫 No genre specified",
        "",
        "",
    ),
    "romance": (
        "💑 Romance",
        "Warm, tender, hopeful. Lighting soft and golden. Expressions open, longing, and vulnerable. Environment intimate and inviting.",
        "Structure: characters meet → tension/obstacle → emotional breakthrough → resolution. Focus on emotional connection, small gestures, eye contact. Build desire slowly.",
    ),
    "melodrama": (
        "💔 Melodrama",
        "Emotionally intense, bittersweet, tearful. Lighting dim or overcast. Expressions anguished, devastated, or desperate. Rain, cold, or darkness as atmosphere.",
        "Structure: happiness → loss or betrayal → suffering → sacrifice or acceptance. Emphasize emotional pain, misunderstanding, longing. Characters are torn between love and duty.",
    ),
    "slice_of_life": (
        "🏠 Slice of Life",
        "Gentle, warm, everyday. Natural lighting, soft colors. Expressions relaxed and genuine. Ordinary settings feel rich and comfortable.",
        "Structure: no dramatic arc — focus on small meaningful moments, routines, conversations, quiet realizations. Authenticity over drama. Time passes naturally.",
    ),
    "action": (
        "⚔️ Action",
        "High energy, urgent, visceral. Harsh directional lighting, dynamic angles. Expressions intense, determined, or in pain. Motion, sweat, dust, chaos.",
        "Structure: setup → escalating conflict → confrontation → resolution. Fast pacing. Each shot should show clear cause and effect. Stakes must be physical and immediate.",
    ),
    "mystery_thriller": (
        "🕵️ Mystery / Thriller",
        "Tense, shadowy, unsettling. High contrast lighting, cold color palette. Expressions guarded, suspicious, or fearful. Environments feel watched or dangerous.",
        "Structure: inciting incident → clues and misdirection → rising dread → revelation. Withhold information deliberately. Every shot should raise a question or deepen unease.",
    ),
    "fantasy": (
        "🌌 Fantasy",
        "Wondrous, epic, otherworldly. Dramatic cinematic lighting, vivid saturated colors. Expressions awed, determined, or mythic. Magic and scale in every environment.",
        "Structure: ordinary world → call to adventure → trials → transformation → climax. World-building must feel real and internally consistent. Power, prophecy, and sacrifice are common themes.",
    ),
    "sci_fi": (
        "🚀 Sci-Fi",
        "Cold, sleek, or gritty depending on tone. Blue-tinted or neon lighting. Expressions analytical, isolated, or haunted. Technology and vast spaces dominate.",
        "Structure: status quo → technological or social disruption → moral dilemma → consequence. Explore the human cost of progress, isolation, or loss of identity.",
    ),
    "dark_romance": (
        "🖤 Dark Romance",
        "Intense, dangerous, obsessive. Deep shadows, rich jewel tones or stark contrast. Expressions possessive, conflicted, yearning. Beauty and menace coexist.",
        "Structure: forbidden attraction → power struggle → dark turning point → surrender or confrontation. Consent may be blurred. Explore obsession, control, and the seduction of danger.",
    ),
    "horror": (
        "😱 Horror",
        "Dread, isolation, wrongness. Extreme chiaroscuro, sickly greens or deep reds. Expressions terrified, in denial, or broken. Something is always wrong in the environment.",
        "Structure: normalcy → first sign of wrongness → escalating dread → confrontation with the horror → aftermath (rarely safe). Psychological horror builds through suggestion; visceral horror through explicit threat.",
    ),
    "erotic": (
        "🔞 Erotic",
        "Sensual, warm, indulgent. Soft warm lighting, golden or candlelit. Expressions aroused, playful, uninhibited. Bodies and textures are the focus — silk, skin, sweat.",
        "Structure: tension and desire → seduction → consummation → afterglow (optional). Build arousal through anticipation. Describe sensation, touch, breath, and heat explicitly. Consent is present and enthusiastic.",
    ),
    "dark_erotic": (
        "🔞⛓️ Dark Erotic",
        "Intense, transgressive, psychologically charged. High contrast dramatic lighting. Expressions conflicted, overwhelmed, or surrendering. Power, vulnerability, and transgression are explicit.",
        "Structure: power imbalance established → escalating intensity → crossing a line → aftermath of surrender or resistance. Explore dominance, submission, coercion, and psychological complexity without softening. Explicit and unflinching.",
    ),
    "drama": (
        "🎭 Drama",
        "Grounded, emotionally layered, real. Natural or muted lighting. Expressions complex — people hide their feelings but body language betrays them. Environments feel lived-in.",
        "Structure: established relationships → conflict or revelation → confrontation → changed dynamic. Focus on dialogue subtext, moral complexity, and human contradiction. No easy answers.",
    ),
    "comedy": (
        "😂 Comedy",
        "Bright, energetic, expressive. High-key warm lighting. Expressions exaggerated, surprised, or gleeful. Physical comedy, absurd situations, timing.",
        "Structure: setup → complication → escalation → punchline or reversal. Timing matters. Characters react to absurdity with sincerity. Embarrassment, misunderstanding, and irony drive the story.",
    ),
}

_ALL_SIZES = [
    ("[1:1] 640 × 640",    640,  640),
    ("[1:1] 768 × 768",    768,  768),
    ("[1:1] 1024 × 1024", 1024, 1024),
    ("[1:1] 1536 × 1536", 1536, 1536),
    ("[1:1] 2048 × 2048", 2048, 2048),
    ("[3:2] 768 × 512",    768,  512),
    ("[3:2] 960 × 640",    960,  640),
    ("[3:2] 1536 × 1024", 1536, 1024),
    ("[3:2] 1920 × 1280", 1920, 1280),
    ("[2:3] 512 × 768",    512,  768),
    ("[2:3] 640 × 960",    640,  960),
    ("[2:3] 1024 × 1536", 1024, 1536),
    ("[2:3] 1280 × 1920", 1280, 1920),
    ("[4:3] 640 × 480",    640,  480),
    ("[4:3] 768 × 576",    768,  576),
    ("[4:3] 1024 × 768",  1024,  768),
    ("[4:3] 1440 × 1080", 1440, 1080),
    ("[3:4] 480 × 640",    480,  640),
    ("[3:4] 576 × 768",    576,  768),
    ("[3:4] 768 × 1024",   768, 1024),
    ("[3:4] 1080 × 1440", 1080, 1440),
    ("[16:9] 640 × 360",   640,  360),
    ("[16:9] 1280 × 720", 1280,  720),
    ("[16:9] 1920 × 1080",1920, 1080),
    ("[16:9] 2304 × 1296",2304, 1296),
    ("[9:16] 360 × 640",   360,  640),
    ("[9:16] 720 × 1280",  720, 1280),
    ("[9:16] 1080 × 1920",1080, 1920),
    ("[9:16] 1296 × 2304",1296, 2304),
    ("[21:9] 1260 × 540", 1260,  540),
    ("[21:9] 2048 × 878", 2048,  878),
]
_SIZE_MAP = {lbl: (w, h) for lbl, w, h in _ALL_SIZES}

# ภาษาที่เขียนด้วยอักษรละติน — ใช้ตัดสินว่าเมื่อผู้ใช้เลือกภาษานี้เป็น Description Language แล้ว
# ตัวอักษรนอกอักษรละตินที่หลุดมาใน output ถือเป็นภาษาต้นฉบับที่โมเดลลืมแปล ไม่ใช่ภาษาที่สั่ง
LATIN_SCRIPT_LANGS = {"en", "vi", "id", "ms", "es", "fr", "de", "pt", "it", "tr", "nl", "pl"}

def is_latin_script_lang(code: str) -> bool:
    return (code or "en").strip() in LATIN_SCRIPT_LANGS
