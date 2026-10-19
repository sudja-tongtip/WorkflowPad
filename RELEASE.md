# WorkflowPad Release Versioning

Version format: `MAJOR.MINOR.PATCH`

During 0.x development:

- **PATCH** — bug fixes / small corrections
- **MINOR** — new features or meaningful behavior changes
- **1.0.0** — first stable production release

Each app owns its own version and is released independently. Nothing forces the apps to advance together.

| App               | `APP_ID`            | Where `APP_VERSION` lives          |
|-------------------|---------------------|------------------------------------|
| Story Generator   | `story_generator`   | `story_generator/constants.py`     |
| Prompt Lab        | `prompt_lab`        | `prompt_lab.py`                    |
| Image Editor      | `image_editor`      | `image_editor.py`                  |
| Gallery Generator | `gallery_generator` | `gallery_generator.py`             |
| Batch Editor      | `batch_editor`      | `batch_editor.py`                  |

`update.json` (repository root) is keyed by `APP_ID`. `app_version.py` provides `parse_version()` and
`is_newer_version()` for the future update checker (numeric comparison: `0.10.0` is newer than `0.9.0`).

## Git tags / GitHub Release names

The portable ZIP ships all five apps together, so one release = one tag:

```
v0.1.6
```

- GitHub Release title: `WorkflowPad v0.1.6 Beta`, with "Set as a pre-release" ticked while the version is 0.x.
- ZIP name: `WorkflowPad-v0.1.6-Beta.zip` (`RELEASE_NAME` in `make_release.py`).
- `APP_VERSION` and `update.json` stay plain `MAJOR.MINOR.PATCH` (no `-beta` suffix): `app_version.parse_version()` only accepts digits.
- Each app still owns its own `APP_VERSION`; the release tag is the highest version that the ZIP contains.

## When the version changes

Versions change when **preparing a release**, not on every commit. The program never edits its own version.

Normal development: edit → commit → edit → commit.

Release: change `APP_VERSION` → update `update.json` → commit release → create Git tag → create GitHub Release.

## Release checklist

1. Update `APP_VERSION`
2. Update `update.json`
3. Run tests
4. Commit
5. Create the app-specific Git tag
6. Create the GitHub Release
7. Upload the packaged ZIP/EXE
8. Put the final GitHub Release download URL into `update.json`
9. Commit/push `update.json`
