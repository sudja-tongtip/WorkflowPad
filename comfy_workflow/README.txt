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
  image_create-text-to-image_krea2.json  comfy_apis\image_create\    pictures from text (all programs)
  image_edit-flux2.json               comfy_apis\image_edit\         editing a picture from a source image, no mask (Story Generator Inpaint Mode, Batch Editor)
  image_edit_mask-flux2.json          comfy_apis\image_edit\         editing a masked part of a picture (Image Editor)
  enhance_face-restore-upscale.json   comfy_apis\enhance\            face restore / upscale (Image Editor: Enhance, Scale & Export)
  enhance_flux_fill_outpaint.json     comfy_apis\enhance\            extend a picture beyond its edges / outpaint (Image Editor: Extend; needs a Pad Image for Outpainting node)
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
                    editing workflows (comfy_apis\image_edit\) also need a Load Image node; text-to-image ones (comfy_apis\image_create\) must not need one
  llm workflows     one LLMSessionChatNode (it gives the chat memory). The program fills in the text for you.
  caption workflows a Load Image node and a node that outputs the text (for example JoyCaption)
  enhance workflows a Load Image node and a Save Image node

If a workflow does not show up in a program, check the folder and the file name first, then that it was exported with
Export (API).


Custom nodes you may need to install in ComfyUI
-----------------------------------------------
  image_edit-flux2.json       none beyond a recent ComfyUI (it uses a Subgraph and ComfySwitchNode)
  image_edit_mask-flux2.json  InpaintCropImproved / InpaintStitchImproved (ComfyUI-Inpaint-CropAndStitch), Image Comparer (rgthree-comfy)
  enhance_face-restore-...    face restore nodes (FaceRestoreModelLoader, FaceRestoreCFWithModel), ColorMatchV2
  enhance_flux_fill_outpaint  ComfyUI-GGUF (LoaderGGUF) for the Flux Fill model
  caption_joycaption.json     JoyCaption GGUF nodes (JC_GGUF_adv, JC_ExtraOptions), ComfyUI-Custom-Scripts (ShowText/SaveText)
  llm_gemma.json              LLMSessionChatNode (a ComfyUI custom node for local GGUF chat)

The image examples are built with Subgraphs, so they need a ComfyUI version recent enough to open them (the Subgraph feature).
Install them from ComfyUI-Manager (search the node name). Models are separate downloads, for example from Hugging Face
or Civitai; check each model's own license.


Notes
-----
- Your exported files stay on your computer. WorkflowPad never uploads them.
- Keep a copy of your own workflows: a fresh download of WorkflowPad starts with empty comfy_apis folders.
