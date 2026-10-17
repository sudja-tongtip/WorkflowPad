BRIEF: LLM-PIPELINE-VNEXT-PROFESSIONAL-TEAM-A1

GOAL

Create a CLEAN vNext story-to-image pipeline built around one simple idea:

A1, A2, B, and Fill are competent specialist workers.

We do not program their thinking step-by-step.

For each worker we provide only:

1. ROLE
2. MATERIAL / CONTEXT
3. BUSINESS GOAL
4. OUTPUT FORM

Then the worker THINKS and fills the form.

Python transports and validates the form.
Python is NOT a creative worker.

PRIMARY PRODUCT GOAL:

THE GENERATED IMAGE SEQUENCE TELLS THE STORY WELL.

A viewer looking at the images in chronological order should broadly understand:

- who is involved,
- what happens,
- what changes,
- how characters react,
- how the situation develops,
- where the climax is,
- and how the story ends.

This matters more than satisfying a large collection of internal prompt rules.


==================================================
0. THIS IS A RESET, NOT ANOTHER PATCH
==================================================

Do NOT continue stacking rules onto the current A1/A2/B/Fill prompts.

Create a CLEAN vNext path.

Suggested conceptual names:

A1_VNEXT
A2_VNEXT
B_VNEXT
FILL_VNEXT

Reuse existing infrastructure where useful:

- provider calls
- Local / Grok workflows
- UI
- settings
- JSON transport
- project storage
- logging
- retries
- Krea2 generation
- image display/save

Do NOT automatically reuse old creative prompt-rule blocks.

The current legacy pipeline may remain available for regression while vNext is tested.

Do not delete the old pipeline until vNext is proven.


==================================================
1. CORE PHILOSOPHY
==================================================

ONE STAGE = ONE PROFESSIONAL JOB.

Every LLM worker receives:

ROLE
MATERIAL
GOAL
OUTPUT FORM

Then:

LET THE WORKER THINK.

Do not prescribe a long reasoning recipe.

Do not tell the worker how to solve every possible failure case.

Do not convert every past bug into another permanent prompt rule.

Python validates:

- valid JSON
- required fields
- count
- IDs
- ordering
- field types

Python does NOT validate creativity by interpreting prose.


==================================================
2. TEAM STRUCTURE
==================================================

The vNext team is:

STORY
  ↓
A1 — STORY SUPERVISOR
  ↓
USER CHOOSES COUNT
  ↓
A2 — SEQUENCE EDITOR
  ↓
B — VISUAL SHOT DESIGNER
  ↓
FILL — KREA2 PROMPT ARTIST
  ↓
IMAGE


Ownership:

A1 = UNDERSTAND THE STORY

A2 = EDIT THE STORY INTO A VISUAL SEQUENCE

B = DESIGN EACH IMAGE

FILL = WRITE THE IMAGE PROMPT


No two workers should own the same creative decision.


==================================================
3. A1 — STORY SUPERVISOR
==================================================

ROLE

A1 is the story supervisor for the visual-production team.

A1 reads the complete story and prepares the shared production bible that the
other workers need.


BUSINESS GOAL

Understand the story accurately enough that the rest of the team can create a
coherent visual adaptation without guessing basic story facts.

A1 is NOT creating shots.

A1 is NOT directing cameras.

A1 is NOT decomposing every sentence into events.


A1 SHOULD THINK ABOUT

- who the recurring characters are
- what they look like
- their starting clothes / distinctive visual identity
- the physical world
- important locations
- recurring objects
- important continuity across the story
- important changes that later images must remember
- the natural visual length of the story


A1 INPUT

- complete source story
- selected mode: Cinema or Manga
- genre/style settings
- user requirements when relevant


A1 OUTPUT FORM

Use a compact form such as:

{
  "recommended_count": 12,
  "recommended_reason": "...",
  "story_summary": "...",
  "character_sheet": "...",
  "world_sheet": "...",
  "locations": "...",
  "important_objects": "...",
  "continuity_notes": "..."
}


FIELD INTENT

recommended_count:
A1's professional recommendation for the natural final sequence length.

Cinema:
number of final images.

Manga:
number of final pages.

recommended_reason:
briefly explain why.

story_summary:
the causal story, not literary prose.

character_sheet:
stable visual identity of recurring people.

world_sheet:
overall visual/physical world continuity.

locations:
important recurring places and how they differ/connect.

important_objects:
story-significant persistent objects.

continuity_notes:
plain-language facts the rest of the team must remember.

Example continuity note:

"Qingru begins with a jade shawl, indigo robe, ivory blouse and crimson skirt.
The jade shawl stays with the old man after she gives it to him.
The thief's charcoal cloak is his own garment, not Qingru's."

This is HUMAN-READABLE production memory.

Do NOT introduce a database language unless later E2E evidence proves it is needed.


==================================================
4. A1 RECOMMENDED COUNT
==================================================

A1 uses professional story judgment.

Do not calculate count from:

number of paragraphs
number of source blocks
fixed events-per-shot math

Think about:

- causal actions
- important state changes
- meaningful reactions
- location/time transitions
- reveals
- opening
- development
- climax
- ending

Pure decoration usually rides inside another beat.

Cinema recommendation asks:

"How many individual images would naturally tell this story well?"

Manga recommendation asks:

"How many pages would naturally tell this story well?"

Several beats can share one Manga page.

Do not use a hardcoded Python formula.


==================================================
5. USER COUNT DIALOG
==================================================

The count dialog belongs:

AFTER A1
BEFORE A2

Flow:

A1
→ recommended count + reason
→ user accepts or edits
→ A2

Example:

Recommended: 14 shots

Reason:
"The story has four major encounters, three irreversible changes, a distinct
climax and ending."

User may enter:

10
14
20
etc.


IMPORTANT

There is NO A2 run before the user makes this choice.

If only the count changes later:

reuse A1
→ rerun A2
→ rerun B

Do not rerun A1 merely because the shot/page count changed.


==================================================
6. A2 — SEQUENCE EDITOR
==================================================

ROLE

A2 is a professional film/storyboard editor.

A2 receives:

- complete source story
- A1 production bible
- selected mode
- exact final count N

A2 decides HOW TO TELL THE STORY in exactly N visual units.


BUSINESS GOAL

Create the strongest understandable visual sequence possible within the selected
length.

The sequence must feel like storytelling, not an inventory of story sentences.


A2 IS RESPONSIBLE FOR

- choosing the meaningful beats
- deciding where visual cuts occur
- deciding what receives its own image
- deciding what can be compressed
- deciding where reactions matter
- maintaining beginning → development → climax → ending
- using the requested count intelligently


A2 IS NOT RESPONSIBLE FOR

- filling clothing_state
- exact character spacing
- detailed location rendering
- final camera prompt wording
- Krea2 syntax


==================================================
7. A2 OUTPUT FORM
==================================================

Keep it simple.

Cinema example:

{
  "sequence": [
    {
      "shot": 1,
      "beat": "...",
      "purpose": "...",
      "presentation": "..."
    }
  ]
}

Exactly N entries.


beat:
What happens in this image.

It must be concrete enough that B understands the moment.

purpose:
Why this image exists in the sequence.

Examples:

"establish the journey"
"make the first handover unmistakable"
"show her reaction after losing the shawl"
"build toward the climax"
"show the final consequence"

presentation:
A2's visual/editorial intention.

Examples:

"wide establishing image"
"focus on both hands during the exchange"
"close emotional reaction"
"environment-heavy reveal"

This is intention, not a technical camera contract.


==================================================
8. A2 PROFESSIONAL FREEDOM
==================================================

If user selects fewer images than A1 recommended:

A2 intelligently compresses.

Preserve the causal story.

Do not mechanically merge source blocks.

Do not force multiple impossible times into one still image.

It is acceptable for a less-important transition to happen between images when the
next image makes the result understandable.


If user selects more images:

A2 intelligently expands useful moments.

Possible expansion:

- anticipation
- action
- reaction
- consequence
- spatial transition
- meaningful detail

Do not create filler merely to satisfy count.


IMPORTANT

Exact N is a FORMAT requirement.

How to make the story work at N is A2's professional responsibility.

Do NOT teach A2 arithmetic recipes such as:

"split at least target_count - source_blocks"
"every block must have a second angle"
"merge block E2+E3"

Those are implementation instructions, not editorial thinking.


==================================================
9. MANGA A2
==================================================

Same worker, different job context.

For Manga:

1 sequence entry = 1 PAGE.

Example:

{
  "shot": 3,
  "beat": "The old man requests the shawl; Qingru hesitates, then gives it to him.",
  "purpose": "Complete the first loss and end the page on the handover.",
  "presentation": "A page with a readable request → hesitation → transfer progression."
}

A2 decides PAGE rhythm.

A2 does not decide exact panels.

Existing Page Planner can remain responsible for panel design.


==================================================
10. DO NOT FORCE SOURCE-BLOCK COVERAGE
==================================================

Source segmentation may remain available for:

- debug
- traceability
- source preservation

But it must NOT become a creative quota.

Do not require:

every source block appears in an A2 parent field.

A2's goal is:

TELL THE STORY.

Not:

SATISFY BLOCK COVERAGE.

If eight source blocks naturally become five images, that is valid.

If one rich passage naturally becomes three images, that is valid.

No creative decision should exist merely to satisfy source-block arithmetic.


==================================================
11. B — VISUAL SHOT DESIGNER
==================================================

ROLE

B is the visual shot designer / storyboard artist.

A2 has already chosen the story beat.

B does NOT re-edit the sequence.

B asks:

"Given this beat, what must the actual image show?"


B INPUT

Provide:

- A1 production bible
- complete A2 sequence for overall context
- THIS A2 beat
- previous resolved B shot when useful for continuity
- next A2 beat may optionally be provided as context only

Do not send a giant collection of obsolete candidate structures.


BUSINESS GOAL

Design a clear, drawable image that:

- communicates the selected story beat
- keeps recurring identities consistent
- keeps important objects/states consistent
- gives characters readable performance
- fits naturally with adjacent images


==================================================
12. B OUTPUT FORM
==================================================

Keep the form useful but understandable.

Suggested vNext form:

{
  "shot": 1,
  "visible_people": "...",
  "location": "...",
  "shot_desc": "...",
  "performance": "...",
  "clothing": "...",
  "objects": "...",
  "camera": "...",
  "lighting": "...",
  "narration": "...",
  "subtitle": ""
}


FIELD INTENT

visible_people:
who is actually visible in THIS image.

location:
where the visible action occurs.

shot_desc:
complete plain-language description of what the image should show.

performance:
what the visible people are physically/emotionally doing.

clothing:
current visible clothing state.

objects:
important current objects and who/where they are.

camera:
useful framing/composition intention.

lighting:
current useful lighting.

narration:
story text associated with this image.

subtitle:
spoken dialogue if needed.


IMPORTANT

These are fields for a professional to fill.

Do not turn each field into a miniature rule engine.


==================================================
13. B SHOULD THINK, NOT COPY
==================================================

A2 may say:

"Qingru gives the jade shawl to the old man."

B should think:

- both need to be visible
- their hands should make the exchange readable
- the old man should react appropriately
- Qingru's remaining clothes should be correct
- the shawl should visibly belong to the exchange
- framing should make the action understandable

We do NOT need to pre-program every one of those decisions.

That is B's job.


==================================================
14. B PERFORMANCE
==================================================

Images are storytelling images, not character-sheet poses.

B should make visible people perform the current beat.

Same character:

may smile in one shot,
hesitate in another,
recoil in another,
cry later,
be exhausted later,

while remaining recognizably the same person.

Character consistency must NOT mean emotional sameness.


==================================================
15. FILL — KREA2 PROMPT ARTIST
==================================================

ROLE

Fill is an image-prompt specialist who knows Krea2.

Fill does NOT decide story structure.

Fill does NOT change B's beat.

Fill turns the completed B shot form into the strongest self-contained Krea2 prompt.


BUSINESS GOAL

Krea2 should produce an image that clearly communicates the shot.

The image should:

- show the right people
- show the current action
- preserve character identity
- preserve current clothing/object state
- feel consistent with the world
- use framing effectively
- remain visually alive


==================================================
16. FILL INPUT
==================================================

Provide:

- THIS completed B form
- relevant A1 character/world identity
- selected image style
- prompt-format setting if the product still exposes one

Avoid feeding Fill:

- old event planning systems
- source-block quotas
- state-delta instructions
- B-selection rules
- repair-history text
- validator explanations

Fill should receive the finished visual brief, not the production bureaucracy.


==================================================
17. FILL PROMPT CONTRACT
==================================================

The live system prompt should be conceptually close to:

"You are the image-prompt artist for Krea2.

Turn the supplied shot brief into one strong, self-contained image prompt.

Your goal is for the generated image to clearly communicate this shot's story beat
while remaining visually consistent with the sequence.

Use professional judgment about wording, composition, emphasis and level of detail.

Preserve the supplied character identity, current clothing/object state, action,
expression, location and camera intention.

Make the characters visibly perform the beat.
Do not reduce real actions or reactions into generic static portrait poses.

Write the final Krea2 prompt in English.

Return only the prompt."


That is the TARGET LEVEL OF COMPLEXITY.

Do not immediately rebuild Minimal V3's large rule stack inside vNext.


==================================================
18. CHARACTER / LOCATION LOCKS
==================================================

Continuity is still important.

But continuity should be supplied as useful production context, not as an enormous
compliance ritual.

Fill may naturally repeat stable character and location facts when useful.

The prompt may contain compact identity/location anchors.

Do not require a large hierarchy of:

IDENTITY LOCK
LOCATION LOCK
SPATIAL LOCK
INTERACTION LOCK
FINAL IDENTITY CHECK

unless clean E2E tests later prove a specific one is necessary.

Start simple.


==================================================
19. PYTHON'S JOB
==================================================

Python is the production coordinator.

Python MAY:

- call A1/A2/B/Fill
- parse JSON
- check required fields
- check types
- check exact A2 count
- check sequence numbering
- check duplicate IDs
- transport fields
- store results
- batch calls
- retry malformed output
- log inputs/outputs
- pass previous-shot context
- call Krea2

Python MUST NOT:

- decide which story beat is important
- create or merge story events
- infer garment meaning
- decide who owns an object from prose
- choose character emotion
- rewrite B's performance
- invent clothing
- insert creative actions
- repair prompt semantics with keyword tables
- choose camera by round-robin
- rewrite a weak prompt into a stronger story interpretation


If an LLM worker's FORM is structurally invalid:
ask that worker to correct it.

If its CREATIVE WORK is weak:
improve that worker's role/goal/context.

Do not make Python become the worker.


==================================================
20. CLEAN RETRY MODEL
==================================================

Retries should be simple.

STRUCTURAL FAILURE:

"Your previous response did not match the required form:
- expected 12 sequence entries, received 10
- item 4 missing 'beat'

Return the complete corrected form."

Do not send pages of validator philosophy.


CREATIVE FAILURE during development:

Identify which worker first lost the goal.

Then improve only that worker's brief/context.

Do not add downstream repairs to hide upstream mistakes.


==================================================
21. LEGACY RULE ISOLATION
==================================================

For vNext, do not automatically invoke legacy helpers whose purpose is to
semantically repair LLM output.

Especially audit live calls related to:

- clothing prose reconstruction
- garment keyword tables
- prompt semantic insertion
- spacing prose repair
- camera round-robin assignment
- state reconstruction
- old candidate coverage
- state_delta
- ARM compression rules
- source-block creative quotas

Do not delete them yet.

Simply keep them OUT of the clean vNext lane unless a specific E2E failure proves
one is necessary.


==================================================
22. IMPLEMENTATION STRATEGY
==================================================

Do not rewrite the entire application at once.

Implement vNext as a parallel production lane.

Suggested:

pipeline_version = "legacy" | "vnext"

Build:

_build_a1_vnext_prompt()
run_a1_vnext()

_build_a2_vnext_prompt()
run_a2_vnext()

_build_b_vnext_prompt()
run_b_vnext()

_build_fill_vnext_prompt()
run_fill_vnext()

Names may differ, but keep vNext creative prompts isolated from legacy prompt-rule
assemblies.

Avoid calling old mega-builders and trying to remove rules conditionally.
That will recreate the same complexity.


==================================================
23. UI FLOW
==================================================

vNext UI:

START
→ A1
→ show recommended count/reason
→ user selects count
→ A2
→ B
→ Fill
→ Images


If user changes only count:

reuse A1
→ A2
→ B
→ Fill


If user edits source story:

rerun A1 onward.


==================================================
24. DEBUGGING
==================================================

For each vNext run save the raw work of each worker.

A1_RAW
A2_RAW
B_RAW
FILL_RAW

And normalized form.

Do not hide failed attempts.

Debug review should answer:

Where did the story first become wrong?

A1?
A2?
B?
Fill?
Renderer?


==================================================
25. E2E DEVELOPMENT PROVIDER
==================================================

LOCAL FIRST.

Use the real production Local provider:

gemma4_llm

Do not tune architecture against Grok first.

The clean architecture must work acceptably with Local.


==================================================
26. E2E IMAGE TEST
==================================================

Development render resolution:

1024 x 720

This is sufficient.

Do not spend development time rendering the full production resolution.

Keep the currently proven Krea2 workflow/model settings otherwise unchanged.


==================================================
27. LOCAL ITERATION
==================================================

Run one complete reference story:

A1
→ count selection
→ A2
→ B
→ Fill
→ Krea2 images

Inspect the COMPLETE chronological image sequence.

If it fails:

find FIRST LOSS.

Example:

A1 misunderstood who owns the garment
→ A1 problem

A1 correct, A2 skipped the important handover
→ A2 problem

A2 correct, B made everyone stand still
→ B problem

B correct, Fill removed the action
→ Fill problem

Fill prompt correct, Krea2 failed physically
→ renderer problem


Fix the worker that failed.

Do not patch the whole pipeline.


==================================================
28. STORYTELLING ACCEPTANCE
==================================================

Do NOT pass vNext merely because JSON is correct.

Look at the generated images in order.

Judge:

OPENING
Can we understand the starting situation?

PROGRESSION
Does each important image advance or develop the story?

ACTION
Can we see what characters are actually doing?

REACTION
Do characters respond naturally to what happens?

CONTINUITY
Do recurring characters, clothes, objects and places remain understandable?

VARIETY
Do changing beats produce changing performances/compositions rather than repeated
static portraits?

CLIMAX
Is the major turning/high point visually apparent?

ENDING
Does the sequence visibly arrive somewhere?

OVERALL
Could a viewer broadly retell the story from the pictures?


==================================================
29. TEST MULTIPLE COUNTS
==================================================

After the natural recommended count passes:

Test one LOWER count.

Example:
recommended 14
test 9

A2 should intelligently compress while preserving the causal story.


Test one HIGHER count.

Example:
recommended 14
test 20

A2 should intelligently expand useful story moments without creating filler.


Do not change A1 between these tests.


==================================================
30. MANGA TEST
==================================================

After Cinema Local is healthy:

test Manga Local.

A1 recommends pages.

User selects pages.

A2 creates exactly N page-level story beats.

B designs page-level visual content.

Existing Page Planner may design panels.

Goal remains:

the rendered pages tell the story.


==================================================
31. GROK TEST
==================================================

ONLY AFTER LOCAL vNext is successful.

Then run the SAME source and selected count with Grok.

Do not change the prompts specifically for Grok first.

Compare:

LOCAL
vs
GROK

Report differences in:

A1 story understanding
A2 storytelling/editing
B visual design
Fill prompts
final image sequence


==================================================
32. WHAT NOT TO DO DURING INITIAL VNEXT
==================================================

Do NOT initially add:

STATE_LEDGER_V2
state_delta
source-block quotas
SPINE/COVERAGE taxonomy
ARM budget
event-to-shot arithmetic
semantic Python repair
garment dictionaries for creative decisions
camera round-robin
performance classifiers
large negative-rule catalogs
dozens of final-check issue types

If an actual vNext E2E failure proves a missing capability:

first ask:

"Could the responsible worker solve this if its JOB BRIEF or INPUT were clearer?"

Only add a new structural mechanism when the answer is no.


==================================================
33. PROMPT DESIGN RULE
==================================================

Each vNext worker prompt should be readable like a real work brief.

Recommended shape:

ROLE
GOAL
INPUT EXPLANATION
A SMALL NUMBER OF RESPONSIBILITIES
OUTPUT FORM

Avoid long sections of:

NEVER...
MUST...
EXACTLY...
IF X THEN Y...
EXCEPTION...
COUNTER-EXCEPTION...

The form can be strict.

The thinking should remain flexible.


==================================================
34. FINAL REPORT
==================================================

Return:

A. LEGACY TRACE

Identify which old A1/A2/B/Fill functions currently carry the main complexity.

Do not change them yet unless shared infrastructure must be touched.


B. VNEXT FUNCTIONS

List exact new functions/files.


C. PROMPTS

Show the complete live prompts for:

A1 vNext
A2 vNext
B vNext
Fill vNext

They should be short enough for a human to understand quickly.


D. FORMS

Show exact JSON contracts for each stage.


E. UI FLOW

Prove:

A1
→ count dialog
→ A2

No A2 before count selection.


F. LOCAL E2E

Provider:
gemma4_llm

Resolution:
1024x720

Show:

A1 output
selected count
A2 sequence
B forms
Fill prompts
image outputs


G. IMAGE STORY REVIEW

Opening              PASS/PARTIAL/FAIL
Progression          PASS/PARTIAL/FAIL
Action               PASS/PARTIAL/FAIL
Reaction             PASS/PARTIAL/FAIL
Character continuity PASS/PARTIAL/FAIL
Object continuity    PASS/PARTIAL/FAIL
Location continuity  PASS/PARTIAL/FAIL
Performance variety  PASS/PARTIAL/FAIL
Climax               PASS/PARTIAL/FAIL
Ending               PASS/PARTIAL/FAIL
Overall storytelling PASS/PARTIAL/FAIL


H. FIRST-LOSS ITERATIONS

For every correction during Local testing:

visible failure
→ first worker that lost it
→ narrow brief/context change
→ result


I. LOWER / HIGHER COUNT TEST

Report both.


J. MANGA LOCAL

Report page storytelling result.


K. GROK

Run only after Local success.
Report separately.


L. COMPLEXITY COMPARISON

Legacy:
- prompt size / approximate complexity
- A2/B overlap
- semantic Python repair involvement

vNext:
- prompt size / approximate complexity
- stage ownership
- semantic Python repair involvement


M. VERDICT

VNEXT ARCHITECTURE:
KEEP / PARTIAL / REVERT

LOCAL STORYTELLING:
KEEP / PARTIAL / REVERT

MANGA:
KEEP / PARTIAL / REVERT

GROK:
KEEP / PARTIAL / REGRESSION


==================================================
35. FINAL PRINCIPLE
==================================================

We are not building four rule-following parsers.

We are building a small production team.

A1 understands.
A2 edits.
B designs.
Fill renders.

Give each worker a clear job,
good material,
a clear goal,
and a form to complete.

Then let them think.

The program coordinates the workers.

It does not do their creative jobs for them.

FINAL SUCCESS CRITERION:

PUT THE GENERATED IMAGES IN ORDER.

DO THEY TELL THE STORY?