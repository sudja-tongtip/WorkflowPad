comfy_workflow - set up ComfyUI for WorkflowPad
================================================

WorkflowPad does not generate images by itself. It sends work to YOUR ComfyUI. The files in this folder are
example ComfyUI workflows. You open one in ComfyUI, point it at the models you own, test it, then export it
for WorkflowPad. The files do not contain any model: every model field says SELECT_YOUR_MODEL.


The 4 steps
-----------
1. Open ComfyUI (Desktop) and drag one of the .json files in this folder onto the canvas.
   (If ComfyUI asks about missing nodes, install them with ComfyUI-Manager first: see "Custom nodes" below.)
2. In every loader node that says SELECT_YOUR_MODEL, choose a model you have. Press Run once.
   If it makes an image (or text), the workflow works.
3. Menu: Workflow > Export (API). Save the file. Use the plain "Export (API)", not "Save".
4. Put the exported file in the matching folder of comfy_apis\ (see the table), then start WorkflowPad.
   The file name shows up in the program's workflow lists. No restart of ComfyUI is needed.


Which file goes where
---------------------
  example in this folder              put your exported file in      used for
  ----------------------------------  -----------------------------  ------------------------------------
  image_text-to-image.json            comfy_apis\image\              pictures from text (all programs)
  image_edit-identity.json            comfy_apis\image\              editing a picture (needs a source image)
  enhance_face-restore-upscale.json   comfy_apis\enhance\            face restore / upscale (Image Editor)
  caption_joycaption.json             comfy_apis\caption\            image -> prompt (Image Editor, Story Generator)
  llm_gemma.json                      comfy_apis\llm\                chat / text writing (Story Generator, Prompt Lab)

You can give your exported file any name (keep the .json). For comfy_apis\llm\ the name must end with _llm.json
(for example my_gemma_llm.json), because Prompt Lab lists only those.


What WorkflowPad needs inside a workflow
----------------------------------------
WorkflowPad finds these nodes by what they are, so you can rebuild a workflow as you like, as long as it keeps them:

  image workflows   a text node for the prompt (CLIP Text Encode; give the positive one the title "Positive")
                    a seed (KSampler or RandomNoise), a size (an Empty Latent node), steps (KSampler / SamplerCustomAdvanced)
                    a Save Image node at the end
                    editing workflows also need a Load Image node
  llm workflows     one LLMSessionChatNode (it gives the chat memory). The program fills in the text for you.
  caption workflows a Load Image node and a node that outputs the text (for example JoyCaption)
  enhance workflows a Load Image node and a Save Image node

If a workflow does not show up in a program, check the folder and the file name first, then that it was exported with
Export (API).


Custom nodes you may need to install in ComfyUI
-----------------------------------------------
  image_edit-identity.json    Krea2 edit nodes (Krea2EditGroundedEncode, Krea2EditModelPatch)
  enhance_face-restore-...    face restore nodes (FaceRestoreModelLoader, FaceRestoreCFWithModel), ColorMatchV2
  caption_joycaption.json     JoyCaption GGUF nodes (JC_GGUF_adv, JC_ExtraOptions), ComfyUI-Custom-Scripts (ShowText/SaveText)
  llm_gemma.json              LLMSessionChatNode (a ComfyUI custom node for local GGUF chat)

Install them from ComfyUI-Manager (search the node name). Models are separate downloads, for example from Hugging Face
or Civitai; check each model's own license.


Notes
-----
- Your exported files stay on your computer. WorkflowPad never uploads them.
- Keep a copy of your own workflows: a fresh download of WorkflowPad starts with empty comfy_apis folders.
