# WorkflowPad

Copyright (c) 2026 Sudja. Free software under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).

WorkflowPad is a set of desktop tools (Python + [Flet](https://flet.dev)) that turn a story into images through **your own ComfyUI**. It does not include ComfyUI or any image/LLM model.

| Program | What it does |
|---|---|
| **Story Generator** (`story_generator.py`) | Story → Story Bible → Beat Sheet → Shot List → Image Prompts → pictures |
| **Image Editor** (`image_editor.py`) | Working-folder editor for pictures: edit and enhance with ComfyUI workflows, zoom/pan viewer |
| **Prompt Lab** (`prompt_lab.py`) | Keep and test a list of prompts with your workflows (works together with Image Editor); LLM chat panel |
| **Batch Editor** (`batch_editor.py`) | Edit images with a ComfyUI workflow and a prompt editor |
| **Gallery Generator** (`gallery_generator.py`) | Generate images repeatedly (until stopped or a target count) into a gallery of thumbnails |

## Manual

An illustrated user manual for all five programs: [`docs/WorkflowPad-Manual.html`](docs/WorkflowPad-Manual.html) (download the file and open it in a browser; it is also inside the release ZIP).

## Download (Windows)

Portable build, no Python needed: see the **Releases** page of this repository. Extract the ZIP, run `WorkflowPad.exe`.

## Run from source

Python 3.11+ (tested on 3.14 / Windows 11):

```
pip install -r requirements.txt
python story_generator.py
```

ComfyUI must be running separately (default `127.0.0.1:8188`, changeable in Settings → Connection).

## Set up ComfyUI for WorkflowPad

The programs read ComfyUI workflows exported in **API format**. You make them yourself:

1. Open an example from [`comfy_workflow/`](comfy_workflow) in ComfyUI and choose the models you own.
2. In ComfyUI use **Workflow → Export (API)**.
3. Put the exported file in the matching folder of `comfy_apis/` (`image`, `llm`, `caption`, `enhance`).

Details, the list of custom nodes and what a workflow must contain: [`comfy_workflow/README.txt`](comfy_workflow/README.txt).

## Where your data is stored

Your stories and settings are **not** in the program folder. They are in `%APPDATA%\WorkflowPad` (stories, last sessions) and `%LOCALAPPDATA%\WorkflowPad` (temporary files, optional debug logs, optional detection models). Your exported workflows stay in `comfy_apis/` and are not tracked by git.

## Build the portable release

```
python make_release.py
```

Needs PyInstaller and a CPU build of PyTorch (see `requirements.txt`). Versions: [`RELEASE.md`](RELEASE.md).

## License

AGPL-3.0, see [`LICENSE`](LICENSE). The release also bundles third-party software (including Ultralytics, AGPL-3.0); see `THIRD_PARTY_NOTICES.txt` inside the release ZIP.
