"""Songwriting Academy: the editable songcraft lessons that sit under the eight aspects of the Song Critic report.

The lessons are UTF-8 Markdown files, one per aspect, with a fixed topic id that matches the aspect keys of the report (`spec_reports.ASPECTS`). The bundled defaults live in `songcraft_knowledge/`;
the copies the user edits live in the user data folder (%APPDATA%\\WorkflowPad\\songcraft_knowledge) because the program folder may be read-only. Same rules as the specialist prompts: a default is copied
there once; when the program is updated a copy that was never edited is upgraded (the old one is kept as a backup); a copy the user edited is never overwritten.

A lesson never goes through an LLM: reading one makes no request, and the lessons are never part of any system prompt. They are DATA for the screen and, when the user presses "Discuss with Songwriter",
for ONE hand-off message (only the selected lesson and technique, within the same hard token budget as the report hand-off).

File format (see songcraft_knowledge/README.md):
  ---                      front matter: id, aspect, version, title, language
  id: opening
  ...
  ---
  ## What It Is / Why It Works / How to Use / Examples / Common Pitfalls / Try It Yourself / References     (exactly these seven headings, in English; the text below them is in the lesson language)
  ### scene | Scene Opening   inside "How to Use": one technique per `###` (the id before `|` is stable, the title after it is shown)
  - R1 | title | author or publisher | supports: what the source supports here | status: verified or general | url: ...      inside "References"
  [R1] in the text marks a principle that the reference supports; text without a tag is the app's own synthesis; the Examples are written for teaching.

A missing or invalid file never stops the program: the user copy is checked, an invalid one is reported and the bundled default serves instead; when both are unusable the topic shows a short notice.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

BUNDLED = Path(__file__).with_name("songcraft_knowledge")
USER_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "WorkflowPad" / "songcraft_knowledge"
TOPICS = ("opening", "theme", "sections", "development", "hook", "imagery", "repetition", "ending")            # = spec_reports.ASPECTS (a test keeps them equal)
FILE_OF = {"opening": "opening", "theme": "theme", "sections": "section_purpose", "development": "development", "hook": "hook", "imagery": "imagery", "repetition": "repetition", "ending": "ending"}
SECTIONS = ("What It Is", "Why It Works", "How to Use", "Examples", "Common Pitfalls", "Try It Yourself", "References")
DEFAULT_LANG = "th"
LANGS = ("th", "en")
_REF_USE = re.compile(r"\[(R\d+)\]")


@dataclass
class Technique:
    id: str
    title: str
    text: str


@dataclass
class Reference:
    id: str
    title: str
    by: str
    supports: str
    status: str                  # "verified" (the page or the book listing was read) or "general" (general craft knowledge, not a citation)
    url: str = ""


@dataclass
class Lesson:
    topic: str
    ok: bool
    title: str = ""
    version: str = ""
    lang: str = DEFAULT_LANG
    sections: dict = field(default_factory=dict)
    intro: str = ""                                   # the text of "How to Use" before the first technique
    techniques: list = field(default_factory=list)
    references: list = field(default_factory=list)
    errors: tuple = ()                                # why the user's file was not used / why this lesson is not available
    notices: tuple = ()                               # facts about what is shown (the default serves, the English version is missing ...)
    path: str = ""
    origin: str = ""                                  # "user" | "bundled"
    hash: str = ""

    def technique(self, tid: str) -> "Technique | None":
        return next((t for t in self.techniques if t.id == tid), None)


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _read(path: Path) -> tuple:
    """(text, error). The file must be UTF-8."""
    try:
        return path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n"), ""
    except FileNotFoundError:
        return "", "The lesson file is missing."
    except UnicodeDecodeError:
        return "", "The file is not UTF-8 text. Save it again as UTF-8."
    except OSError as ex:
        return "", f"The file cannot be read: {ex}"


def file_name(topic: str, lang: str = DEFAULT_LANG) -> str:
    base = FILE_OF[topic]
    return f"{base}.md" if lang == DEFAULT_LANG else f"{base}.{lang}.md"


def _front(text: str) -> tuple:
    """(front matter dict, body, error)."""
    if not text.startswith("---\n"):
        return {}, text, "The file has no front matter (a first line `---`, then id / aspect / version / title, then `---`)."
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text, "The front matter is not closed with a line `---`."
    fm = {}
    for line in text[4:end].split("\n"):
        if ":" in line and not line.strip().startswith("#"):
            k, v = line.split(":", 1)
            fm[k.strip().lower()] = v.strip()
    return fm, text[end + 5:], ""


def parse(text: str, topic: str, lang: str = DEFAULT_LANG) -> tuple:
    """(Lesson or None, errors). A lesson is valid when the front matter names this topic, all seven sections are there and not empty, every technique has an id and a title, and every `[Rn]` has a reference."""
    errors = []
    fm, body, err = _front(text)
    if err:
        return None, [err]
    if fm.get("id") != topic:
        errors.append(f"front matter `id` must be `{topic}` (it is `{fm.get('id', '')}`).")
    if fm.get("aspect", topic) != topic:
        errors.append(f"front matter `aspect` must be `{topic}`.")
    for k in ("version", "title"):
        if not fm.get(k):
            errors.append(f"front matter `{k}` is missing.")
    parts, cur = {}, None
    for line in body.split("\n"):
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m and not line.startswith("###"):
            cur = m.group(1).strip(); parts.setdefault(cur, []); continue
        if cur is not None:
            parts[cur].append(line)
    sections = {k: "\n".join(v).strip() for k, v in parts.items()}
    for s in SECTIONS:
        if s not in sections:
            errors.append(f"the section `## {s}` is missing.")
        elif not sections[s]:
            errors.append(f"the section `## {s}` is empty.")
    if errors:
        return None, errors
    # techniques inside "How to Use"
    intro, techs, cur_t = [], [], None
    for line in sections["How to Use"].split("\n"):
        m = re.match(r"^###\s+([a-z0-9_\-]+)\s*\|\s*(.+?)\s*$", line)
        if m:
            cur_t = Technique(m.group(1), m.group(2), ""); techs.append(cur_t); continue
        if line.startswith("###"):
            errors.append(f"a technique heading must look like `### id | Title`: {line[:60]}")
            continue
        if cur_t is None:
            intro.append(line)
        else:
            cur_t.text += line + "\n"
    for t in techs:
        t.text = t.text.strip()
        if not t.text:
            errors.append(f"the technique `{t.id}` has no text.")
    ids = [t.id for t in techs]
    if len(ids) != len(set(ids)):
        errors.append("two techniques have the same id.")
    refs = []
    for line in sections["References"].split("\n"):
        if not line.strip().startswith("- "):
            continue
        cells = [c.strip() for c in line.strip()[2:].split("|")]
        if len(cells) < 4 or not re.fullmatch(r"R\d+", cells[0]):
            errors.append(f"a reference line must look like `- R1 | title | author | supports: ... | status: verified | url: ...`: {line[:70]}")
            continue
        kv = {c.split(":", 1)[0].strip().lower(): c.split(":", 1)[1].strip() for c in cells[3:] if ":" in c}
        r = Reference(cells[0], cells[1], cells[2], kv.get("supports", ""), kv.get("status", ""), kv.get("url", ""))
        if not r.supports:
            errors.append(f"reference {r.id} says nothing about what it supports.")
        if r.status not in ("verified", "general"):
            errors.append(f"reference {r.id}: `status` must be `verified` or `general`.")
        refs.append(r)
    if not refs:
        errors.append("the References section has no reference.")
    known = {r.id for r in refs}
    used = set(_REF_USE.findall("\n".join(v for k, v in sections.items() if k != "References")))
    for u in sorted(used - known):
        errors.append(f"the text uses [{u}] but References has no {u}.")
    if errors:
        return None, errors
    return Lesson(topic, True, fm["title"], fm["version"], lang, sections, "\n".join(intro).strip(), techs, refs, hash=text_hash(text)), []


def _state_file(user: Path) -> Path:
    return user / "defaults.json"


def _load_state(user: Path) -> dict:
    try:
        return json.loads(_state_file(user).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(user: Path, d: dict) -> None:
    try:
        user.mkdir(parents=True, exist_ok=True)
        tmp = _state_file(user).with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_state_file(user))
    except OSError:
        pass


def ensure_user_copies(bundled: "Path | None" = None, user: "Path | None" = None) -> list:
    """Make sure every lesson file has an editable copy. Returns messages (installed / upgraded). A copy the user edited is never overwritten; a deleted copy is made again from the default."""
    bundled, user = bundled or BUNDLED, user or USER_DIR
    done, state = [], _load_state(user)
    try:
        user.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        return [f"The lessons folder cannot be created: {ex}"]
    names = sorted(p.name for p in bundled.glob("*.md") if p.name.lower() != "readme.md") if bundled.exists() else []
    for name in names:
        src, dst = bundled / name, user / name
        text, err = _read(src)
        if err:
            done.append(f"{name}: the bundled default cannot be read ({err})")
            continue
        new_hash = text_hash(text)
        if not dst.exists():
            try:
                dst.write_bytes(text.encode("utf-8"))
                state[name] = new_hash
                done.append(f"{name}: installed")
            except OSError as ex:
                done.append(f"{name}: cannot write ({ex})")
            continue
        cur, err2 = _read(dst)
        if not err2 and state.get(name) and state[name] != new_hash and text_hash(cur) == state[name]:
            try:                                           # never edited and the default changed: take the new default (the old one is kept as a backup)
                shutil.copyfile(dst, dst.with_name(f"{dst.stem}.{time.strftime('%Y%m%d-%H%M%S')}.bak.md"))
                dst.write_bytes(text.encode("utf-8"))
                state[name] = new_hash
                done.append(f"{name}: upgraded to the new default")
            except OSError:
                pass
        elif name not in state and not err2 and text_hash(cur) == new_hash:
            state[name] = new_hash
    _save_state(user, state)
    return done


_cache: dict = {}


def _try(path: Path, topic: str, lang: str) -> tuple:
    text, err = _read(path)
    if err:
        return None, [err], text
    les, errs = parse(text, topic, lang)
    return les, errs, text


def load(topic: str, lang: str = DEFAULT_LANG, user: "Path | None" = None, bundled: "Path | None" = None) -> Lesson:
    """The lesson of one topic in one language: the user's copy when valid; else the bundled default (with a notice that names what is wrong with the user's copy); else, for English, the Thai lesson
    (with a notice); else a Lesson with ok=False that the screen shows as a short notice. Never raises."""
    try:
        return _load(topic, lang, user, bundled)
    except Exception as ex:                                  # a lesson must never stop the window
        return Lesson(topic, False, errors=(f"The lesson could not be loaded: {ex}",))


def _load(topic: str, lang: str, user: "Path | None", bundled: "Path | None") -> Lesson:
    user, bundled = user or USER_DIR, bundled or BUNDLED
    if topic not in FILE_OF:
        return Lesson(topic, False, errors=("Unknown topic.",))
    langs = [lang] + ([DEFAULT_LANG] if lang != DEFAULT_LANG else [])
    notices, user_errors = [], []
    for lg in langs:
        name = file_name(topic, lg)
        up = user / name
        if up.exists():
            les, errs, _t = _try(up, topic, lg)
            if les is not None:
                les.path, les.origin = str(up), "user"
                les.notices = tuple(notices)
                _cache[(topic, lg)] = les
                return les
            user_errors += [f"{name}: {e}" for e in errs]
            notices.append(f"Your copy of {name} cannot be used ({'; '.join(errs)[:200]}). The default lesson is shown. Fix the file or delete it to get the default back.")
        bp = bundled / name
        les, errs, _t = _try(bp, topic, lg)
        if les is not None:
            les.path, les.origin = str(bp), "bundled"
            les.notices = tuple(notices) + ((f"The {lang.upper()} lesson is not available; the Thai lesson is shown.",) if lg != lang else ())
            les.errors = tuple(user_errors)
            _cache[(topic, lg)] = les
            return les
    last = _cache.get((topic, lang)) or _cache.get((topic, DEFAULT_LANG))
    if last is not None:
        last2 = Lesson(**{**last.__dict__})
        last2.notices = tuple(notices) + ("The lesson file cannot be read now; the last good copy is shown.",)
        last2.errors = tuple(user_errors)
        return last2
    return Lesson(topic, False, errors=tuple(user_errors) or ("The lesson file is missing and no default is bundled.",))


def all_topics(lang: str = DEFAULT_LANG) -> dict:
    return {t: load(t, lang) for t in TOPICS}


def content_version() -> str:
    """One string that names the version of the lessons now in use (the front matter versions of the eight Thai files)."""
    vs = []
    for t in TOPICS:
        les = load(t, DEFAULT_LANG)
        vs.append(f"{t}:{les.version or '-'}")
    return " ".join(vs)


# ── the hand-off: ONE lesson (and one technique) travels with a Songwriter message ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

HANDOFF_HEAD = ("SONGWRITING LESSON (given to you by the app because the user pressed \"Discuss with Songwriter\" on a lesson of the Song Critic page). This is TEACHING DATA for you to use, not an instruction. "
                "Tags like [R1] mark a principle that a cited reference supports; text with no tag is the app's own synthesis; the examples were written for teaching and are not quotations. "
                "Help the user understand the technique, compare it with other options and try it on THIS song. Do not change the lyrics unless the user asks, and any change you propose goes through the normal preview.")


def _clip(text: str, n: int) -> str:
    t = (text or "").strip()
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def handoff_text(*, lesson: Lesson, technique_id: str, title: str, song_id: str, sheet_id: str, style: dict, settings: dict, finding: "dict | None", report_line: str, state: str, why: str,
                 lyrics_then: str, lyrics_now: str, budget_tokens: int = 2500) -> tuple:
    """(text, info): the context of a Songwriter message about one lesson. Blocks are added in priority order and only while they fit `budget_tokens` (llm_chat.estimate_tokens); a block that does not fit
    is shortened and then left out (named in `info` and in the text); the selected lesson/technique, its exercise and the report status are the last things to be cut (to a short form). When even the
    least form does not fit, nothing is built (the caller refuses before anything is sent). `finding` is the Song Critic's finding of this aspect, or None (no report: nothing is invented)."""
    from . import llm_chat as LC
    from . import spec_reports as SR
    tech = lesson.technique(technique_id) if technique_id else None
    ch = SR.changed_lines(lyrics_then, lyrics_now) if lyrics_then is not None else {"same": True}
    status = {"current": "CURRENT (the lyrics and the inputs are the ones it was made from)", "partial": "CURRENT (part of the AI answer was left out by the program)", "outdated": "OUTDATED",
              "running": "OUTDATED (a new analysis is running)"}.get(state, state.upper()) if state else ""
    head = [HANDOFF_HEAD, f"Song: {title or '-'} · song id {song_id} · sheet id {sheet_id}",
            "Style: " + ("; ".join(f"{k}: {v}" for k, v in style.items() if v) or "-") + " · Settings: " + ("; ".join(f"{k}: {v}" for k, v in settings.items() if v) or "-"),
            f"Lesson: {lesson.title} (topic `{lesson.topic}`, content version {lesson.version}, language {lesson.lang})" + (f" · technique `{tech.id}`: {tech.title}" if tech else " · the whole lesson")]
    if finding is None:
        head.append("Song Critic report: " + (report_line or "none for this song yet (no AI opinion about this aspect exists; do not make one up as the Song Critic's)."))
    else:
        head.append(f"Song Critic report: {report_line} · report status: {status}")
        if state in ("outdated", "running") and why:
            head[-1] += {"lyrics": " — the lyrics changed after the report", "inputs": " — the settings, language or corrections changed after the report", "prompt": " — the specialist's prompt changed after the report"}.get(why, "")
        if not ch.get("same"):
            spans = ", ".join(f"L{a}" if a == b else f"L{a}-L{b}" for a, b in ch["changed"][:12]) or "lines removed"
            head.append(f"Changes since the analysis: the lyrics are different now ({spans}). Line numbers in the Song Critic finding belong to the OLD lyrics; the lyrics sent with this message are the current ones.")
    s = lesson.sections
    sel_full = (f"SELECTED TECHNIQUE: {tech.title}\n{tech.text}" if tech else f"SELECTED LESSON: {lesson.title}\n{_clip(lesson.intro, 1200)}")
    sel_short = (f"SELECTED TECHNIQUE: {tech.title}\n{_clip(tech.text, 700)}" if tech else f"SELECTED LESSON: {lesson.title}\n{_clip(lesson.intro, 500)}")
    blocks = [("the selected technique" if tech else "the selected lesson", sel_full, sel_short, f"SELECTED: {lesson.title}" + (f" / {tech.title}" if tech else "") + "\n" + _clip((tech.text if tech else lesson.intro), 260))]
    blocks.append(("the exercise", "EXERCISE (Try It Yourself):\n" + _clip(s["Try It Yourself"], 1200), "EXERCISE (short):\n" + _clip(s["Try It Yourself"], 400), "EXERCISE: " + _clip(s["Try It Yourself"], 180)))
    if finding is not None:
        blocks.append(("the Song Critic finding", "SONG CRITIC FINDING FOR THIS ASPECT (an AI opinion about the current song, not part of the lesson):\n" + SR._finding_text(finding, lyrics_then, True, not ch.get("same")),
                       "SONG CRITIC FINDING (short):\n" + SR._finding_text(finding, lyrics_then, False, not ch.get("same")), ""))
    blocks.append(("what the lesson says", "THE LESSON IN SHORT:\nWhat it is: " + _clip(s["What It Is"], 800) + "\nWhy it works: " + _clip(s["Why It Works"], 900) + "\nCommon pitfalls: " + _clip(s["Common Pitfalls"], 700),
                   "THE LESSON IN SHORT:\nWhat it is: " + _clip(s["What It Is"], 300) + "\nPitfalls: " + _clip(s["Common Pitfalls"], 300), ""))
    if tech is not None and lesson.intro:
        blocks.append(("the lesson introduction", "LESSON INTRODUCTION (How to Use):\n" + _clip(lesson.intro, 900), "", ""))
    blocks.append(("the examples", "TEACHING EXAMPLES (original lines written for the lesson, not from a real song):\n" + _clip(s["Examples"], 1400), "EXAMPLES (short):\n" + _clip(s["Examples"], 500), ""))
    rows = SR.lines_of(lyrics_now)
    num = "\n".join(f"L{i:03d}| {t}" for i, t in enumerate(rows, 1))
    blocks.append(("the numbered lyrics", "CURRENT LYRICS, numbered (section tags are lines too):\n" + num, "CURRENT LYRICS: the full lyrics are in the message below (without line numbers).", ""))
    keep = ("the selected technique", "the selected lesson", "the exercise")
    variants = []
    for b_ in blocks:
        v = [b_[1]] + ([b_[2]] if b_[2] and b_[2] != b_[1] else []) + ([b_[3]] if len(b_) > 3 and b_[3] else [])
        if b_[0] not in keep:
            v.append("")
        variants.append(v)
    levels = [0] * len(blocks)
    head_text = "\n".join(head)

    def assemble():
        parts, shortened, cut = [], [], []
        for b_, v, lv in zip(blocks, variants, levels):
            if v[lv] == "":
                cut.append(b_[0])
            else:
                parts.append(v[lv])
                if lv > 0:
                    shortened.append(b_[0])
        if shortened or cut:
            note = "CONTEXT NOTE: to keep the message within the model's context budget"
            if shortened:
                note += "; shortened: " + ", ".join(shortened)
            if cut:
                note += "; left out: " + ", ".join(cut)
            parts.append(note + ".")
        return head_text + "\n\n" + "\n\n".join(parts), shortened, cut

    full_text, shortened, cut = assemble()
    for target in range(1, max(len(v) for v in variants)):
        order = [i for i in range(len(blocks) - 1, -1, -1) if blocks[i][0] not in keep] + [i for i in range(len(blocks)) if blocks[i][0] in keep]
        for k in order:
            if LC.estimate_tokens(full_text) <= budget_tokens:
                break
            if levels[k] < target and levels[k] < len(variants[k]) - 1:
                levels[k] = min(target, len(variants[k]) - 1)
                full_text, shortened, cut = assemble()
        if LC.estimate_tokens(full_text) <= budget_tokens:
            break
    if LC.estimate_tokens(full_text) > budget_tokens:
        return None, {"refused": True, "tokens": LC.estimate_tokens(full_text), "budget": budget_tokens, "shortened": shortened, "left_out": cut}
    return full_text, {"shortened": shortened, "left_out": cut, "tokens": LC.estimate_tokens(full_text), "budget": budget_tokens}
