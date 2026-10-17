PATCH BRIEF — Minimal V3 Fill: Shared Cinematic THINK + Previous Visual Context

GOAL

Rebuild Minimal V3 Fill around ONE shared visual reasoning process used by
ALL output formats.

Do not patch Narrative/Tag/Sectioned independently.

Architecture:

CURRENT CANONICAL SHOT
        +
PREVIOUS VISUAL CONTEXT (previous generated prompt, when available)
        ↓
SHARED THINK
        ↓
1. MOMENT
2. SUBJECT & PERFORMANCE
3. BLOCKING
4. COMPOSITION
5. CAMERA
6. WORLD INTERACTION
7. CONTINUITY VERIFY
        ↓
RESOLVED VISUAL SHOT
        ↓
FORMAT RENDERER
        ↓
Narrative / Tag Style / Sectioned Detailed / Manga Page

Core principle:

THINK ONCE → RENDER DIFFERENTLY.

Renderer may change representation, never interpretation.

This is a REWRITE / CONSOLIDATION patch.
Do NOT append the new pipeline on top of the old serializer architecture.
The resulting shared Fill prompt should be materially shorter.


==================================================
1. CURRENT CANONICAL SHOT
==================================================

Treat the current canonical shot as the source of truth.

Read supplied information such as:

- visible people
- scene_desc
- expression
- spacing/contact
- current clothing
- props/owners
- place/environment
- time/light
- shot_scale
- permanent identity

These are INPUT FACTS / CONSTRAINTS.

They are NOT a list of output categories that must each receive prose.

Do not begin by asking:

"What fields must I mention?"

Begin by understanding:

"What single visual moment do these facts describe?"


==================================================
2. PREVIOUS VISUAL CONTEXT — WIRE IT NOW
==================================================

Pass exactly ONE previous successfully generated shot prompt to Fill when
available.

Use the existing previous completed shot output if that is the cleanest
available source.

Expected conceptual input:

PREVIOUS VISUAL CONTEXT:
<previous generated prompt>

For the first shot:

PREVIOUS VISUAL CONTEXT:
none

This context is READ-ONLY VISUAL HISTORY.

Its purpose is to understand what the viewer was just shown and improve
shot-to-shot visual continuity.

It may help with:

- continuity of screen space
- previously visible environment
- visual progression
- avoiding accidental visual discontinuity
- understanding how the current beat follows the previous image

STRICT AUTHORITY:

CURRENT CANONICAL SHOT ALWAYS WINS.

Never carry forward previous:

- action
- pose
- expression
- contact
- clothing state
- prop state/owner
- location
- time
- lighting
- camera/framing

unless the CURRENT canonical shot still supports it.

Previous prompt must never repair or fill a missing current story fact.

Do not copy its wording.

Do not emit PREVIOUS VISUAL CONTEXT into the final image prompt.

Do not send it to the image model as a second prompt/reference section.

It exists only as input to Fill's reasoning.


==================================================
3. MOMENT — WHAT EXACT INSTANT IS THIS IMAGE?
==================================================

`scene_desc` is the narrative/visual spine.

Resolve:

- What important event is happening RIGHT NOW?
- Who is doing what to whom/what?
- Who is actor, action, and target?
- What phase of the supplied action is visually occurring?
- What reaction belongs to that instant?
- If the supplied action spans time, which visible instant best communicates
  THIS beat in one still image?
- What did the viewer just see in the previous shot, if available, and how
  does that help understand the current instant?

Previous context may clarify continuity.

It may NOT invent the current event.

Do not reconstruct unseen story beats.

Result:

ONE resolved visual moment.

Not a field list.


==================================================
4. SUBJECT & PERFORMANCE
==================================================

Determine how each visible person performs the resolved Moment.

Use current canonical action/expression/state.

Consider as useful:

- action
- reaction
- face
- eyes/gaze
- mouth/jaw
- head reaction
- gesture
- body response/tension
- interaction with another person
- interaction with an established prop
- who initiates
- who receives
- who responds

These are NOT mandatory output categories.

Choose only cues that make the Moment readable.

Expression supports the Moment.

Expression must never:

- create another independent action
- replace the main action
- reverse actor/action/target
- invent an unsupported major pose

Performance must arise from the story Moment, not from a decorative pose.


==================================================
5. BLOCKING
==================================================

Resolve the physical geometry of the SAME Moment.

Use scene_desc + supplied spacing/contact + prop ownership.

Consider:

- front / behind
- left / right
- near / far
- depth
- orientation when supported
- physical contact
- who touches whom
- prop location/owner
- action direction

Blocking exists to make the action visually readable.

Spacing provides geometry/contact.

Spacing does NOT:

- create a new action
- invent a pose
- decide actor/target
- reverse action direction

An action-like phrase inside spacing may confirm established interaction but
must not redefine scene_desc's action.


==================================================
6. COMPOSITION
==================================================

Compose the resolved Moment inside the frame.

Think about:

- primary visual focus
- secondary visual focus
- relative subject prominence
- foreground
- midground
- background
- depth
- useful negative space
- how much established environment should be visible
- how blocking can make actor/action/target immediately readable

Composition decides how EXISTING facts are presented.

It must not create:

- new people
- new objects
- new architecture
- new scenery
- new events

Do not output a COMPOSITION section unless the selected renderer explicitly
requires such formatting.


==================================================
7. CAMERA
==================================================

Ask:

"From where should the viewer see the already-resolved Moment?"

Respect supplied shot_scale.

Resolve only presentation choices that are not otherwise specified, such as
a neutral camera position/angle when necessary.

Consider:

- shot scale
- angle
- camera height/position
- perspective
- depth
- subject prominence
- environmental visibility

Camera serves Moment + Composition.

Camera must never change canonical action/state.

Shot scale controls framing/detail, not story content.

Literal camera/scale wording should appear only where the selected renderer
contract requires it; do not duplicate it merely because Camera considered
it internally.


==================================================
8. WORLD INTERACTION
==================================================

Use the ESTABLISHED world to make the SAME Moment feel physically present.

Do not treat place/environment/time/light as independent fields that must be
recited.

Instead consider how established world state interacts with the moment:

- established wind affecting hair/clothing
- established light falling on subjects/environment
- established crowd/background activity around the action
- established dust/rain/sweat/atmosphere interacting with bodies/movement
- established props/clothing responding naturally to established action

These are reasoning categories, not mandatory details.

Never invent a new:

- environmental feature
- weather condition
- crowd
- light source
- prop
- event

World supports the Moment.


==================================================
9. CONTINUITY VERIFY
==================================================

After resolving the visual shot, verify it ONCE against current canonical
truth.

Check:

- visible cast/count
- sex binding
- permanent appearance/body
- current clothing
- clothing state/change
- props and ownership
- location/environment
- time
- light sources
- action direction
- actor/action/target
- spacing/contact
- supplied physical state

Also compare against Previous Visual Context ONLY to detect accidental
continuity problems.

If previous and current disagree:

CURRENT WINS.

Missing current information remains missing when filling it would create a
story fact.

Do not invent merely to make the image feel complete.

Consolidate existing duplicate guardrails here instead of repeating the same
rule in AUTHORITY/SCALE/COVERAGE/format branches.


==================================================
10. CINEMATIC RESOLUTION VS UNSUPPORTED GUESS
==================================================

Fill is expected to make visual-directing decisions where canonical facts do
not specify representation.

Allowed RESOLUTION includes choices such as:

- visual focus
- prominence
- foreground/background organization
- neutral camera placement
- useful negative space

provided they do not change story facts.

Do NOT guess when the missing information would decide or alter:

- who acts
- who receives
- important pose/action
- contact
- clothing
- prop ownership
- identity
- location/time
- story outcome

Such information remains unspecified.


==================================================
11. DEBUG VISUAL RESOLUTION
==================================================

When Fill debug logging/mode is enabled, request a SHORT decision summary
alongside the final result.

This is NOT a request for hidden chain-of-thought.

It is a compact audit record of the resolved visual decisions.

Target shape:

DEBUG VISUAL RESOLUTION
Moment: <one short line>
Performance: <one short line>
Blocking: <one short line>
Composition: <one short line>
Camera: <one short line>
World: <one short line>
Continuity: PASS | ISSUE: <short reason>
Assumptions: none | <short explicit assumptions and reasons>

FINAL PROMPT:
<normal selected-format output>

Keep every debug line concise.

Do not output intermediate deliberation.


==================================================
12. DEBUG ASSUMPTIONS
==================================================

When Fill had to resolve an unspecified cinematic choice, report it briefly.

Example class:

Assumption: neutral eye-level camera.
Reason: angle unspecified; neutral presentation preserves the supplied action.

Distinguish:

SUPPORTED
= directly established/supported by canonical data.

RESOLVED
= cinematic presentation choice required to compose/frame the supplied
  facts without changing them.

UNCERTAIN
= missing information whose resolution could change story meaning.

Do not guess UNCERTAIN story facts.

Report the uncertainty instead.

The normal final image prompt must not contain these labels.


==================================================
13. PRODUCTION MODE
==================================================

When debug mode is OFF:

Do NOT output:

- Moment analysis
- Performance analysis
- Blocking analysis
- Composition analysis
- Camera analysis
- World analysis
- Continuity report
- Assumptions
- Previous Visual Context

Return only the normal final prompt required by the selected renderer.


==================================================
14. RENDER — ONLY AFTER THINK IS COMPLETE
==================================================

Only after the visual shot has been resolved and verified should the selected
format affect output.

All renderers receive the SAME semantic interpretation.

NARRATIVE:
render the resolved shot as coherent visual prose.

TAG_STYLE:
render the same resolved shot as compact visual tags/fragments.

SECTIONED_DETAILED:
place the same resolved shot into the required sections.

MANGA_PAGE:
represent the same resolved visual information using its required page/panel
contract.

Renderer may:

- phrase
- compress
- order
- label
- structure

Renderer may NOT:

- reinterpret scene_desc
- choose a different pose/action
- reverse actor/target
- reinterpret spacing
- change clothing/props
- create different environmental interaction

THINK ONCE.
RENDER DIFFERENTLY.


==================================================
15. SLIM FORMAT-SPECIFIC SEMANTIC RULES
==================================================

Audit identified semantic reasoning currently duplicated inside format
branches, especially expression/spacing/action interpretation.

Move shared semantic meaning into the THINK stages above.

After this patch, format branches should ideally contain only:

- required syntax
- section/layout order
- prose vs tags
- required labels
- IDENTITY LOCK placement
- parser-required formatting

Do not leave separate semantic interpretations in Narrative/Tag/Sectioned
that can produce different visual meaning from the same canonical shot.

Do not unnecessarily rewrite Manga-specific layout mechanics.


==================================================
16. REMOVE OLD SERIALIZER ARCHITECTURE
==================================================

Remove/rewrite:

"You are a serializer..."

Remove the old field-recitation mindset.

Do NOT preserve a long field-by-field AUTHORITY list if its semantics are
already represented cleanly by shared THINK + VERIFY.

Remove/rewrite COVERAGE language that implies:

"every supplied field must receive prompt text."

Canonical facts must remain true.

They do NOT each require an independent phrase.


==================================================
17. DEDUPLICATE
==================================================

Collapse repeated rules covering:

- identity persistence
- cast persistence
- clothing authority
- no invention
- pose invention
- actor/action/target
- spacing semantics
- scale preservation

Each semantic rule should have one clear home whenever possible:

THINK
VERIFY
or RENDER.

Do not add the new architecture while leaving all old equivalent wording
underneath it.


==================================================
18. DO NOT CREATE A NEW STAGE SERIALIZER
==================================================

The stages are INTERNAL reasoning priorities.

Do NOT require final output such as:

MOMENT:
PERFORMANCE:
BLOCKING:
COMPOSITION:
CAMERA:
WORLD:

except for the compact DEBUG VISUAL RESOLUTION when debug mode is enabled.

The final image prompt must read according to the selected renderer, not
according to the internal reasoning pipeline.


==================================================
19. NO INTERMEDIATE JSON REQUIRED
==================================================

Do not create a new Resolved Visual Shot JSON/schema/parser unless existing
code architecture makes it strictly necessary.

Prefer conceptual:

THINK → resolved internal visual interpretation → RENDER

within the same Fill generation.

We are not replacing the old canonical serializer with another serializer.


==================================================
20. PRESERVE REQUIRED MECHANICAL CONTRACTS
==================================================

Preserve anything required by downstream parsing/checking, including where
applicable:

- exact people-count requirements
- IDENTITY LOCK syntax
- required renderer labels/order
- Final Check/debug parsing contracts that are actually consumed

Semantic cleanup must not silently break mechanical parsing.


==================================================
21. SCOPE
==================================================

Patch Minimal V3 Fill only.

Do NOT modify:

- Outline semantics
- Groom
- Checker semantics
- unrelated parser behavior
- retry strategy
- Python semantic repair

Minimal payload wiring for Previous Visual Context and minimal parsing needed
for DEBUG VISUAL RESOLUTION are allowed if required.


==================================================
22. SIZE REQUIREMENT
==================================================

This patch is REPLACEMENT + CONSOLIDATION.

Measure assembled system-prompt size before and after for:

- narrative
- tag_style
- sectioned_detailed
- manga_page

Target:

The new prompt should be materially SHORTER despite adding Composition,
World Interaction, and Previous Visual Context semantics.

If it grows substantially, stop and identify which old rules were not
actually replaced.


==================================================
FINAL REPORT
==================================================

Return:

1. Exact functions/constants changed.
2. Actual runtime path used to supply Previous Visual Context.
3. Exact source of previous prompt.
4. First-shot behavior.
5. Confirmation current canonical overrides previous context.
6. Old ROLE → new ROLE.
7. Old AUTHORITY/COVERAGE architecture → replacement.
8. Final shared THINK stages implemented.
9. Moment semantics.
10. Performance semantics.
11. Blocking semantics.
12. Composition semantics.
13. Camera semantics.
14. World Interaction semantics.
15. Continuity Verify semantics.
16. Debug Visual Resolution implementation.
17. Assumption/uncertainty reporting behavior.
18. Production-mode output behavior.
19. Semantic rules removed from each renderer.
20. Mechanical renderer contracts preserved.
21. Any semantic rules that could NOT be moved out of a renderer, and why.
22. Confirmation no intermediate JSON/schema was added, or exact justification
    if one became necessary.
23. System-prompt chars before → after for all four formats.
24. Payload-size effect of Previous Visual Context separately from system
    prompt size.
25. py_compile result.
26. Static regression check using at least one representative canonical shot:
    verify Narrative / Tag / Sectioned resolve the SAME Moment,
    actor/action/target, Blocking, and clothing state.
27. Live regression if available; otherwise explicitly state unavailable.

STOP after this patch.
Do not begin tuning individual renderer prose quality yet.