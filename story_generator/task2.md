BRIEF — Fix Step 2 Shot Budget / ARM Compression Contract

GOAL

Step 1 creates the COMPLETE event timeline.

Example:
17 source events

User may then choose a smaller final shot count:

17 events
→ user selects 8 shots
→ ARM selects/compresses the full timeline
→ Step 2 outputs exactly 8 shots

IMPORTANT:
17 events does NOT mean Step 2 must output 17 shots.

The current bug is conflicting instructions:
- "17 events, 8 shots"
- "every event gets its own shot"
- "last event lands on shot 8"

This can cause the LLM to simply output E1–E8 and lose E9–E17.

────────────────────────────
1. SEPARATE EVENT COUNT FROM SHOT BUDGET
────────────────────────────

Define clearly:

EVENTS
= complete chronological source timeline from Step 1.

SHOT BUDGET
= number of final image shots selected by the user.

Example:

source_events = 17
shot_budget = 8

Expected:
exactly 8 output shots.

Do NOT require one event per shot when:

shot_budget < event_count

────────────────────────────
2. REMOVE CONFLICTING RULE
────────────────────────────

When ARM/compression mode is active, remove/disable instructions such as:

"every event gets its own shot"

"[SPINE] and [COVERAGE] alike each receive one shot"

"recommended_shots must equal events"

Those rules belong only to 1:1 sequential mode.

They must NOT appear in the Step 2 prompt when the user selected a smaller shot budget.

────────────────────────────
3. ADD EXPLICIT ARM CONTRACT
────────────────────────────

Step 2 should receive a clear instruction:

USER SHOT BUDGET = 8.

The source events are the complete story timeline.
They are NOT a requirement for the same number of output shots.

Use the selected ARM policy to compress/select the COMPLETE
event timeline into exactly 8 final image shots.

Do not simply take the first 8 events.

────────────────────────────
4. FULL STORY COVERAGE IS MANDATORY
────────────────────────────

ARM selection must consider:

E1 → E17

not:

E1 → E8

The selected 8 shots must represent the complete narrative arc:

BEGINNING
→ DEVELOPMENT
→ IMPORTANT STATE CHANGES
→ CLIMAX
→ ENDING

The final selected shot must reach the end of the source timeline.

Example:

BAD:

Shot 1 → E1
Shot 2 → E2
...
Shot 8 → E8

E9-E17 disappear.

GOOD example:

Shot 1 → E1
Shot 2 → E2-E3
Shot 3 → E4-E5
Shot 4 → E6
Shot 5 → E7-E10
Shot 6 → E11-E13
Shot 7 → E14-E16
Shot 8 → E17

This is only an example.
ARM decides the actual grouping.

────────────────────────────
5. PRESERVE IMPORTANT SPINE EVENTS
────────────────────────────

ARM should prioritize:

1. opening/setup required to understand the scene
2. major SPINE actions
3. state-changing events
4. major location changes
5. major character/prop/clothing state changes
6. climax
7. ending/final state

COVERAGE events may be:
- merged into adjacent compatible shots
- represented by a stronger nearby SPINE shot
- omitted when necessary for the selected budget

But do not remove a COVERAGE beat if doing so destroys an essential story transition.

────────────────────────────
6. MERGING RULE
────────────────────────────

Multiple adjacent events may map to one shot when they can be represented as ONE drawable frozen moment.

Example:

E4:
Girl walks to mirror

E5:
Girl stops before mirror and answers Photographer

May become:

Shot:
Girl standing before the mirror while answering Photographer.

covers:
E4,E5

But do NOT combine events that require incompatible moments.

Example:

E6:
Girl lifts clothing

E7:
Photographer later raises camera

If they cannot truthfully exist in the same frozen image,
do not force them together merely to save a shot.

ARM must choose which event deserves the shot or merge elsewhere.

────────────────────────────
7. COVERS MUST SUPPORT MULTIPLE EVENTS
────────────────────────────

Step 2 `covers` must support grouped events.

Preferred structured representation:

"covers": ["E4", "E5"]

If changing schema is undesirable, existing string representation may be:

"covers": "E4,E5"

or:

"covers": "E4-E5"

But choose ONE canonical representation and use it consistently.

Structured array is preferred if downstream code supports it.

────────────────────────────
8. COVERS MEANS SOURCE TRACEABILITY
────────────────────────────

`covers` does NOT mean:

"this shot must reproduce every action from all listed events"

It means:

"these source timeline events were represented/compressed into this selected visual beat."

This allows ARM compression without stuffing multiple sequential actions into one image.

────────────────────────────
9. PRESERVE CHRONOLOGICAL ORDER
────────────────────────────

Selected shots must remain chronological.

Example:

Shot 3 covers E6
Shot 4 covers E9
Shot 5 must not return to E4.

Grouping should normally contain adjacent or closely related events.

Do not create arbitrary cross-timeline bundles such as:

Shot 4 covers E3,E14

unless there is an explicit system reason for non-linear storytelling.

────────────────────────────
10. ENDING ANCHOR
────────────────────────────

When:

shot_budget < event_count

the final shot must represent the final story region.

Do NOT allow the selected sequence to terminate halfway through the event list.

For 17 events / 8 shots:

Shot 8 should normally cover E17
or a final group containing E17, such as:

covers: E16,E17

This is an important regression check.

────────────────────────────
11. BEGINNING ANCHOR
────────────────────────────

Likewise, preserve the beginning.

Shot 1 should represent the opening story region.

Do not let ARM select only dramatic middle/end events and lose scene establishment entirely.

────────────────────────────
12. DO NOT FORCE ALL EVENTS INTO COVERS
────────────────────────────

Important distinction:

ARM is allowed to SELECT.

If budget is much smaller than event count, not every COVERAGE event necessarily needs a covers reference.

Example:

17 events → 5 shots

It may be correct to select:

E1
E4
E8
E12
E17

if those are the strongest visual/state-changing beats.

Therefore validation should NOT require:

union(covers) == E1...E17

unless the selected ARM mode explicitly promises full event coverage.

Required instead:
- selection considered the complete E1...E17 timeline
- beginning represented
- important SPINE/state changes represented
- ending represented
- exactly requested number of shots

────────────────────────────
13. ARM MODE MUST BE EXPLICIT IN PROMPT
────────────────────────────

Pass the actual selection mode clearly, for example:

ARM MODE:
Cinematic compression

SOURCE EVENTS:
17

USER SHOT BUDGET:
8

Do not leave the LLM to infer whether it is in:
- sequential 1:1 mode
or
- compressed ARM mode.

────────────────────────────
14. SEQUENTIAL MODE STILL WORKS
────────────────────────────

Do not break existing 1:1 behavior.

If:

17 events
user selects 17 shots
sequential mode

then:

E1 → Shot 1
E2 → Shot 2
...
E17 → Shot 17

The "one event = one shot" rule is valid HERE.

So use conditional contracts:

IF shot_budget >= event_count AND sequential mode:
    one event per shot

IF shot_budget < event_count OR compression mode:
    ARM selection/compression contract

────────────────────────────
15. REMOVE FALSE EVENT-COVERAGE PASS
────────────────────────────

Review current event coverage debug/validator.

Current case produced only E1-E8 but was still effectively allowed through.

For compressed mode, validation should check useful deterministic facts only:

- output shot count == user shot budget
- covers references valid existing event IDs
- covers order does not move backwards
- first selected region reaches beginning
- final selected region reaches ending
- no invalid E18/E99 etc.

Do NOT make Python decide whether E6 was "semantically important enough."

Importance/selection belongs to ARM/LLM.

────────────────────────────
16. NO PYTHON SEMANTIC REPAIR
────────────────────────────

If ARM selection looks bad:

Do NOT have Python rewrite:
- covers
- scene_desc
- event grouping
- selected SPINE
- selected COVERAGE

Python may report structural/debug information only.

Semantic selection remains LLM/ARM responsibility.

────────────────────────────
17. DEBUG OUTPUT
────────────────────────────

When debug is enabled, log:

source_event_count = 17
user_shot_budget = 8
arm_mode = cinematic_compression

selected mapping:

Shot 1 → E1
Shot 2 → E2,E3
Shot 3 → E4,E5
...
Shot 8 → E17

Also log:

first_source_event_reached = E1
last_source_event_reached = E17
output_shot_count = 8
budget_match = PASS

This makes it immediately obvious if the model just selected E1-E8.

────────────────────────────
18. REGRESSION TEST — CURRENT CASE
────────────────────────────

Input:

17 events
user selects 8 shots

Expected:

- exactly 8 shots
- ARM evaluates full E1-E17 timeline
- not simply E1-E8
- sequence remains chronological
- important SPINE/state-changing beats survive
- ending survives
- Shot 8 reaches E17/final story region
- grouped `covers` allowed
- no Python semantic rewrite

FAIL example:

1:E1
2:E2
3:E3
4:E4
5:E5
6:E6
7:E7
8:E8

PASS example:

1:E1
2:E2,E3
3:E4,E5
4:E6
5:E7,E9
6:E10,E12
7:E14,E16
8:E17

Exact mapping is decided by ARM, not hardcoded.

────────────────────────────
19. IMPORTANT — DO NOT TOUCH OTHER STAGES
────────────────────────────

This patch is Step 2 ARM/shot-budget contract only.

Do NOT modify:
- Step 1 Character Review
- Step 1 Outfit Review
- Fill Minimal V3
- Checker
- Fill routing
- clothing repair
- prop repair
- spacing repair

Those are separate issues.

────────────────────────────
DEFINITION OF DONE
────────────────────────────

1. User can select 8 shots from 17 events.
2. Step 2 outputs exactly 8 shots.
3. Prompt no longer says every event requires its own shot in compression mode.
4. ARM considers the complete event timeline.
5. It does not truncate to the first N events.
6. Beginning is represented.
7. Ending is represented.
8. Important SPINE/state-changing beats are prioritized.
9. Multiple events may map to one shot.
10. covers supports that mapping.
11. Sequential 17→17 behavior remains intact.
12. Python does not semantically select or repair shots.
13. Debug clearly displays event→shot mapping.