# KNOWLEDGE_ARCHITECTURE (Song Critic Academy V1)

## Audit of V3 before the change
- Song Critic V3 (not committed yet) already had: the eight aspect keys (`spec_reports.ASPECTS`), the eight aspect tiles of the Songcraft page (`sr_aspect_tile`), the Discuss hand-off (`sr_discuss` -> `handoff_pending` -> `ask_ai` -> `sr_handoff_text` -> `spec_reports.handoff_text`) and the same `rep_open` expand/collapse state.
- V3's only "knowledge" was (a) a `# Songcraft Reference` section inside the CANDIDATE prompt (not installed) and (b) `SONGCRAFT_KNOWLEDGE.md`, a maintainer note in the audit package. Neither is a lesson set the user can read, so nothing was duplicated: the lessons are new, and the V3 principles (verses develop, refrain meaning from context, specific hook, specific imagery, endings) were re-used as ideas, restated in the lesson files with their sources re-checked (RESEARCH_REFERENCES.md).
- The specialist prompts already have a proven pattern for editable files (`specialists.py`: bundled defaults, a user copy in %APPDATA%, upgrade of unedited copies, validation, last good copy). The lessons use the same pattern.

## Files
```
music_studio/songcraft_knowledge/            bundled defaults (shipped with the program; inside the package data)
    README.md                                the format, for the user who edits
    opening.md  opening.en.md                topic id `opening`
    theme.md  theme.en.md                    `theme`
    section_purpose.md  section_purpose.en.md  `sections`  (file name differs, the id is the aspect key)
    development.md  hook.md  imagery.md  repetition.md  ending.md   (+ the same names with `.en.md`)
%APPDATA%\WorkflowPad\songcraft_knowledge\   the user's copies (editable) and `defaults.json` (hash of what was installed)
music_studio/songcraft_academy.py            loader, parser, installer and the hand-off builder (pure; no Flet; no LLM)
```
`opening.md` is the Thai lesson (the default language) and `opening.en.md` the English one. The topic ids are the aspect keys of the report: `opening, theme, sections, development, hook, imagery, repetition, ending` (`AC.TOPICS == SR.ASPECTS` is a test). The technique ids inside "How to Use" are stable (`scene`, `declaration`, `question`, `in-medias-res`, `dialogue`, `contradiction`, `hook-first`, `atmospheric` map to the report's opening pattern keys).

## File format
Front matter (`id`, `aspect`, `version`, `title`, `language`), then seven fixed `##` headings in English: What It Is, Why It Works, How to Use (with `### id | Title` techniques), Examples, Common Pitfalls, Try It Yourself, References. `[R1]` in the text marks a principle a reference supports; a reference line is `- R1 | title | author | supports: ... | status: verified|general | url: ...`. The user-facing labels of the headings come from the UI language files; the text below them is in the lesson language. No lesson content is in `music_studio.py`.

## Rules (and where each is tested)
| rule | how | test |
|---|---|---|
| content is not hard-coded in the UI | the UI reads `AKB.load(topic, lang)`; the only literal texts are labels | U5, K2 |
| the user can edit the Markdown | the user copy is the one that is read | K17, real app (screenshot `560_user_edited_lesson_survives_restart`) |
| a user-edited file is never overwritten at start | `ensure_user_copies` upgrades a copy only when its hash still equals the hash that was installed (unedited) | K18; real app: development lessons upgraded to 1.0.1 with `.bak.md` backups while the edited `hook.md` stayed |
| a missing or invalid file never crashes | invalid user file -> the bundled default is shown with a notice that names the problem; unreadable bytes, no front matter, a missing section, an undefined `[Rn]`, a wrong id are reported; no default either -> a short "not available" note; English missing -> Thai with a notice; `load` never raises | K19-K23, U9 |
| a deleted copy comes back | the next start installs it again | K22 |
| source reference and content version | `references` in every lesson; `version` in the front matter; `AKB.content_version()`; the page shows `Lesson version x.y.z · user/bundled` | K7, U5 |
| knowledge does not depend on a Song | loaded by topic and language only; no song or sheet id in a `Lesson` | K24 |
| AI reports stay per Song / Sheet | nothing about reports changed; the lesson never writes into a report | existing suites |
| lessons are never sent to a system prompt | no production prompt or specialist module imports or mentions the academy; the only time lesson text goes to a model is one hand-off message after the user pressed Discuss | K14, I6 |
| reading a lesson makes no LLM request | no call anywhere in the loader or the page | K13, U1 |

## Upgrade logic
`defaults.json` stores the hash of each file as installed. At start: file missing -> install it; file present, hash equals the installed hash (never edited) and the bundled default changed -> back up to `name.YYYYMMDD-HHMMSS.bak.md` and replace; file edited -> leave it alone. To get a default back the user deletes the file.

## Packaging
`collect_data_files("music_studio")` in `WorkflowPad.spec` already includes data files under the package, so the `.md` lessons are shipped; the build was not run in this task (KNOWN_LIMITATIONS).
