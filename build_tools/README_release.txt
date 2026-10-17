WorkflowPad — Windows portable (extract & run)
=============================================

1. Extract this ZIP to any folder. Your stories and settings are saved in %APPDATA%\WorkflowPad, not in this folder.
2. Double-click WorkflowPad.exe (Story Generator). The other programs are separate EXEs in the same folder:
     ImageEditor.exe      Image Editor (also opens from inside Story Generator)
     PromptLab.exe        Prompt Lab
     BatchEditor.exe      Batch Editor
     GalleryGenerator.exe Gallery Generator
   No Python installation is needed.

ComfyUI is separate
-------------------
Anything that generates images needs a running ComfyUI server (default 127.0.0.1:8188, change it in
Settings -> Connection). WorkflowPad does not include ComfyUI or any image model.

Optional person/face detection (narration box placement)
--------------------------------------------------------
The detection library is included, the model files are not. When you use a PIL narration render the program offers to
download them (about 13 MB), or open Settings -> Detection -> Detection Models. They are saved in
%LOCALAPPDATA%\WorkflowPad\models and only downloaded when you press Install Models. Without them the narration box is
still placed automatically, just without person/face avoidance.

Folders
-------
comfy_workflow\  example ComfyUI workflows + a README that shows how to set up your ComfyUI
comfy_apis\      YOUR exported ComfyUI workflows (Export (API)) go here: image, llm, caption, enhance
icons\           program icons
_internal\ program runtime — do not edit

Your stories and settings are NOT in this folder: they are saved in %APPDATA%\WorkflowPad (Win+R, paste it).
Updating the program never touches them.

Licenses and source
-------------------
WorkflowPad is free software under the GNU Affero General Public License v3.0 (AGPL-3.0, see LICENSE.txt).
Copyright (c) 2026 Sudja. You may obtain the complete source code at:
https://github.com/sudja-tongtip/WorkflowPad
This package bundles third-party software, including Ultralytics (also AGPL-3.0). See THIRD_PARTY_NOTICES.txt and the
licenses folder.
