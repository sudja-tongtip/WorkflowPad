# CHANGED_FILES (Song Critic V3, not committed)

Tracked files modified by this task (task-only hunks are in `PATCH/song_critic_v3_task_only.patch`):
- `music_studio/spec_reports.py`: Songcraft schema, parser, `items_of`, handoff (+152 / -9)
- `music_studio.py`: 8 hunks (Songcraft page, Discuss with aspect/action, handoff call). 5 other hunks in the same file belong to unrelated Export Audio work and are NOT in the patch (`PATCH/OTHER_WORK_music_studio_py_not_part_of_this_task.diff`)
- `music_studio/locales/th.json`: 36 new keys only (the working copy also holds keys of other work; not in the patch)

New files of this task:
- `tests/test_songcraft.py`
- `docs/song_critic_v3/` (this audit: candidate prompt, songs, expected observations, pre-registration, runner, raw requests/responses, parsed reports, screenshots, reports, patch)

Unchanged on purpose: `music_studio/prompts/specialists/song_critic.md` (production prompt), the other specialists' prompts and code, Music Producer, `tests/test_spec_reports.py`, `test_critic_refs.py`, `test_critic_actions.py`.

Not part of this task but present in the working tree (left alone): deleted `comfy_workflow/*.json`, `music_studio/chat_tags.py`, `music_studio/songwriter_tools.py`, `music_studio/audio_export.py`, other docs.
