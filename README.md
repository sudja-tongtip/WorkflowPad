# WorkflowPad

Copyright (c) 2026 Sudja. Free software under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).

WorkflowPad is a set of seven desktop programs (Python + [Flet](https://flet.dev)) that turn a story, a prompt or a folder of pictures into images, and lyrics into songs, through **your own ComfyUI**. It does not include ComfyUI or any image / LLM model: you bring your workflows and models.

| Program | What it does |
|---|---|
| **Story Generator** (`story_generator.py`) | Story → Story Bible → Beat Sheet → Shot List (or manga Page List) → image prompts → pictures. Review and edit every step, check and direct the prompts, chat about the story, render the shots |
| **Image Editor** (`image_editor.py`) | Working-folder picture editor: create, edit, mask-inpaint, extend (outpaint), enhance (face restore / upscale), picture → prompt, zoom / pan viewer, multi-select |
| **Prompt Lab** (`prompt_lab.py`) | A notebook of prompts: rewrite them with a local LLM or Grok, run them against an image workflow |
| **Batch Editor** (`batch_editor.py`) | Apply one workflow and one prompt to many pictures; select pictures like in a file browser and run only those |
| **Music Studio** (`music_studio.py`) | Write lyrics in a section-aware editor, choose a music style (or let an AI recommend directions and write the caption) and make songs with ACE-Step 1.5; keep every take |
| **Simple Chat Studio** (`simple_chat.py`) | Chat with a local LLM workflow: many conversations, system prompts, Markdown, export |
| **Gallery Generator** (`gallery_generator.py`) | Generate pictures again and again from a list of prompts (with `{a|b|c}` wildcards) into a gallery of thumbnails |

## Manual

An illustrated user manual for all seven programs: [`docs/WorkflowPad-Manual.html`](docs/WorkflowPad-Manual.html) (download the file and open it in a browser; it is also inside the release ZIP).

## Download (Windows)

Portable build, no Python needed: see the **Releases** page of this repository. Extract the ZIP, run `StoryGenerator.exe` (Story Generator); the other programs are separate EXEs in the same folder.

## Run from source

Python 3.11+ (tested on 3.14 / Windows 11):

```
pip install -r requirements.txt
python story_generator.py
```

The other programs start the same way (`python image_editor.py`, `python prompt_lab.py`, `python batch_editor.py`, `python gallery_generator.py`, `python music_studio.py`, `python simple_chat.py`). ComfyUI must be running separately (default `127.0.0.1:8188`, changeable in Settings → Connection).

## Set up ComfyUI for WorkflowPad

The programs read ComfyUI workflows exported in **API format**. You make them yourself:

1. Open an example from [`comfy_workflow/`](comfy_workflow) in ComfyUI and choose the models you own (the names in the examples are only hints).
2. Press Run once to check that it works.
3. In ComfyUI use **Workflow → Export (API)**.
4. Put the exported file in the matching folder of `comfy_apis/`.

Each folder holds one kind of workflow, and each program lists in its dialogs the files that suit the job (the Workflow dropdowns find the right files by the nodes in them, not by their names):

| Folder | Holds | Used by |
|---|---|---|
| `comfy_apis/image_create/` | text-to-image workflows (no source picture needed) | Story Generator (render), Image Editor (New), Prompt Lab, Gallery Generator |
| `comfy_apis/image_edit/` | workflows that need a source picture: edit, img2img, inpaint / mask edit | Image Editor (Edit, Mask), Batch Editor, Story Generator (Inpaint / Img2Img mode) |
| `comfy_apis/enhance/` | face restore / upscale, and outpaint (a Pad Image for Outpainting node) | Image Editor (Enhance, Scale & Export, Extend) |
| `comfy_apis/caption/` | picture → text workflows (JoyCaption, Florence2) | Image Editor (Prompt), Story Generator (vision Check / Direct) |
| `comfy_apis/llm/` | LLM chat workflows (a `LLMSessionChatNode`) | Story Generator, Prompt Lab, Music Studio, Simple Chat Studio |
| `comfy_apis/audio/` | ACE-Step 1.5 music workflows | Music Studio |

The example workflows use Subgraphs, so ComfyUI needs to be recent enough to open them. Details, the list of custom nodes and what a workflow must contain: [`comfy_workflow/README.txt`](comfy_workflow/README.txt).

## Notes on the LLM settings

Story Generator can use **Grok** (an API key in Settings → Grok API, shared with Prompt Lab) or a **local LLM workflow**. Settings → LLM has a switch, *Use the program's LLM settings*:

- **On** (default): the program sets the LLM values it was tested with (repeat penalty, temperature, context size) in the copy of your workflow it sends.
- **Off**: the program leaves those values alone and every call uses the values in your own workflow file. Text, answer limits, session handling and timeouts stay under the program's control.

## Where your data is stored

Your stories and settings are **not** in the program folder. They are in `%APPDATA%\WorkflowPad` (stories, last sessions, prompt lists, settings) and `%LOCALAPPDATA%\WorkflowPad` (temporary files, optional debug logs, optional detection models). The Grok key is in `%APPDATA%\StoryGenerator`. Your exported workflows stay in `comfy_apis/` and are not tracked by git.

## Update notice

Once a day each program asks the GitHub releases page of this project whether a newer version exists (one plain web request, nothing about you is sent; GitHub sees your IP address like for any web request). If there is one, a small notice appears with a button that opens the download page in your browser. Nothing is downloaded or installed by the program. It is silent when you are offline.

One switch controls all five programs: Settings → Connection in Story Generator, or the LLM Settings of Prompt Lab, or "Turn off update checks" on the notice itself. The choice, the "skip this version" choice and the last answer are kept in `%APPDATA%\WorkflowPad\update_check.json` (set `"enabled": false` there to turn it off by hand).

## Build the portable release

```
python make_release.py
```

Needs PyInstaller and a CPU build of PyTorch (see `requirements.txt`). Versions: [`RELEASE.md`](RELEASE.md).

## Repository layout

| Path | What |
|---|---|
| `story_generator.py`, `story_generator/` | Story Generator |
| `image_editor.py`, `prompt_lab.py`, `batch_editor.py`, `gallery_generator.py` | the other four image programs |
| `music_studio.py`, `music_studio/`, `simple_chat.py` | Music Studio and Simple Chat Studio |
| `comfy_workflow/` | example ComfyUI workflows and the set-up README |
| `comfy_apis/` | your exported API workflows (only the folder READMEs are tracked) |
| `docs/` | the illustrated manual |
| `make_release.py`, `build_tools/`, `WorkflowPad.spec` | portable release build |
| `update.json`, `app_version.py` | version information for the future update checker |

## License

AGPL-3.0, see [`LICENSE`](LICENSE). The release also bundles third-party software (including Ultralytics, AGPL-3.0); see `THIRD_PARTY_NOTICES.txt` inside the release ZIP.
