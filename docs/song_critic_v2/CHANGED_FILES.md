# CHANGED_FILES — Song Critic V2 (since HEAD b65d3a1; nothing committed)

## Production files
| File | Change |
|---|---|
| `music_studio/spec_reports.py` | `parse_refs`, `refs_label`; finding fields `refs`, `lines_invalid`, `proposal`; Song Critic findings with unreadable lines kept; `proposal` rules; `changed_lines`, `handoff_text` (+192 / −16 lines) |
| `music_studio.py` | finding card (refs chips, labels, proposal Original / Proposed, Preview / Apply, Discuss), `sr_discuss`, `sr_handoff_text`, `sr_ensure_proposal`, `sr_proposal_ui`, `handoff_pending`, the hand-off block in `ask_ai`, `import hashlib` (task hunks: 8, +370 / −12 in the whole file diff, which also holds the unrelated Export Audio work: `PATCH/OTHER_WORK_…diff`) |
| `music_studio/locales/th.json` | 22 Thai strings |
**Not changed:** the production prompt `music_studio/prompts/specialists/song_critic.md`, Music Producer, the Songwriter / chat code (except the one hand-off block in `ask_ai`), the other three specialists' prompts, the parser's contract for them, AppData, settings.

## New / changed tests and docs
`tests/test_critic_refs.py` (new, 62), `tests/test_critic_actions.py` (new, 49), `tests/test_spec_reports.py` (two V1 expectations updated: a Song Critic finding with unreadable lines is now kept), `docs/specialist_reports/REPORT_TAG_CONTRACT.md` (new). `docs/song_critic_v2/` (this audit: songs, prompts, runs, reports).

## Patch
`PATCH/song_critic_v2_task_only.patch` (8 hunks of `music_studio.py`, `spec_reports.py`, the 22 `th.json` keys; hunk headers recomputed), checked on a clean `HEAD` worktree: `git apply --check` without `--recount` passes (`PATCH/clean_head_check.txt`). New files: copies in `PATCH/`. `PATCH/tests_test_spec_reports.diff`. The unrelated hunks are in `PATCH/OTHER_WORK_music_studio_py_not_part_of_this_task.diff`.
