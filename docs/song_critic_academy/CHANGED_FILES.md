# CHANGED_FILES (Song Critic Academy V1, not committed)

Base for "task only": clean HEAD + the Song Critic V3 task patch (V3 is not committed yet). `PATCH/song_critic_academy_task_only.patch` has the tracked/modified files; `PATCH/new_files/` has the new ones.

## New files
- `music_studio/songcraft_academy.py`: loader, parser, installer, hand-off builder (no Flet, no LLM)
- `music_studio/songcraft_knowledge/`: `README.md` and 16 lessons: `opening`, `theme`, `section_purpose`, `development`, `hook`, `imagery`, `repetition`, `ending` as `<name>.md` (Thai) and `<name>.en.md` (English)
- `tests/test_academy.py`: 58 checks (K knowledge, U page, I Discuss)
- `docs/song_critic_academy/`: this package (scripts: `run_web.py`, `restart_web.sh`, `apply_music_studio_edits.py`, `th_keys_patch.py`, `mkpatch_academy.py`, `run_regression.sh`)

## Modified files (task hunks only)
- `music_studio.py`: the import (`AKB`), the start-up install of the lesson copies, the hand-off dispatch for a lesson in `sr_handoff_text`, the three message texts of the hand-off in `ask_ai`, the `ac_*` functions, the lesson block in `sr_aspect_tile`, the Academy list in `sr_render` (no report / older report / unreadable answer)
- `music_studio/locales/th.json`: 26 new keys (`th_keys_added.json`)
- `tests/test_songcraft.py` (V3's test file, itself uncommitted): `tiles()` now means the eight aspect tiles; `lesson_button()` excludes lesson buttons from the Discuss counts; the older-format check (B13) tests for the absence of the review section instead of the absence of `NN ·` tiles. 55/55 still pass.

## Not changed
`music_studio/prompts/specialists/song_critic.md` (and every other prompt), `spec_reports.py` (no change in this task), `specialists.py`, `llm_chat.py`, the Music Producer, the other three specialists, the Songwriter chat code except the hand-off message texts above, `SCHEMA`, Gemma/Grok settings, the session system.

Unrelated work left in the tree and left out of the patch: Export Audio hunks of `music_studio.py`, `chat_tags.py`, `songwriter_tools.py`, other `th.json` keys, deleted `comfy_workflow/*.json`.
