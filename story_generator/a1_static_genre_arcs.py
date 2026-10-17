# -*- coding: utf-8 -*-
"""A1 Static-owned genre arc library. No Groom imports or dependencies.

Every arc has five Macro Event roles: canonical Entry + four consecutive actions.
The roles are adaptable to the source, not compulsory prop, people or garment lists.
"""

_STATIC_GLOBAL_RULES = 'Every later step is an event: Actor -> Action -> Target (if any) -> what it changes in the scene, an object, clothing or the relationship. The body pose at the end of the step follows from the event and must differ clearly from the previous step (body silhouette, support, torso/legs or position); micro-gestures, gaze or feeling alone never count. Preserve hand occupancy, props, wardrobe, contact, distance and feasible transitions; do not invent a partner for solo sources. Follow source constraints; do not force actions of an arc when impossible in this setting.'

_STATIC_GENRE_GOALS = {
    'romance': 'Build earned affection and reciprocal decisions through concrete approaches, cooperation, distance changes and visible relationship choices.',
    'melodrama': 'Translate emotional stakes into visible acts of disclosure, confrontation, sacrifice and consequence rather than extended internal reflection.',
    'slice_of_life': 'Develop ordinary source-grounded activities through distinct physical phases, practical obstacles and modest but visible outcomes.',
    'action': 'Deliver consequential maneuvers, changing tactical positions and grounded obstacles; bold action must respect geography and mechanics.',
    'mystery_thriller': 'Progress by physically investigating grounded evidence and changing investigative action, not by depicting pure speculation as a shot.',
    'fantasy': 'Use source-compatible supernatural phenomena to produce visible decisions, movement and costs without inventing uncontrolled world rules.',
    'sci_fi': 'Let technology and physical constraints drive observable procedures, position changes and concrete results; keep mechanisms grounded.',
    'dark_romance': 'Show difficult attachment, agency and negotiated boundaries through observable choices, without treating coercion as romance.',
    'horror': 'Increase danger through visible responses, altered shelter, defensive movements and consequences; preserve atmosphere and spatial truth.',
    'erotic': 'Create adult, consensual, non-graphic sensual progression with bold physically distinct poses, chosen proximity, self-presentation or optional private clothing transitions. If the source specifically calls for nudity or garment changes, preserve and develop those facts without graphic sexual acts. Never use gazes or emotional escalation as a substitute for action.',
    'dark_erotic': 'Develop adult, consensual, non-graphic bodily vulnerability alongside secrecy, risk and boundaries, expressed through clear physical actions; never imply coercion is consent.',
    'drama': 'Represent conflict and consequential decisions through concrete acts with visibly changing circumstances.',
    'comedy': 'Build distinct physical missteps, escalating practical complications and visible punchlines without impossible mechanics.',
}

_STATIC_GENRE_ARCS = {
    'romance': [
        {
            "id": 'approach_and_choice',
            "name": 'Approach and Choice',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Take a practical step that makes a respectful approach possible (set something down, get up, come closer)',
                'ACTION 2: Offer a concrete invitation or helpful gesture, allowing a response',
                'ACTION 3: Act on the response with an agreed change in proximity or shared activity',
                'ACTION 4 + PAYOFF: Make a visible choice to continue or part, ending in a changed relationship',
            ],
        },
        {
            "id": 'shared_task',
            "name": 'Connection Through a Task',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Begin a joint task using objects already available',
                'ACTION 2: Divide the work or change how it is done when a practical difficulty arises',
                'ACTION 3: Coordinate a decisive action to complete the task',
                'ACTION 4 + PAYOFF: Share an earned moment of connection through a concrete follow-through',
            ],
        },
        {
            "id": 'trust_in_action',
            "name": 'Trust Made Visible',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Move to help with a specific manageable difficulty',
                'ACTION 2: Make a careful supportive offer without presuming contact',
                'ACTION 3: Respond to acceptance or refusal by changing the plan visibly',
                'ACTION 4 + PAYOFF: Resolve the difficulty and show the earned trust in a final action',
            ],
        },
        {
            "id": 'reunion_movement',
            "name": 'A Physical Reunion',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Act on recognizing someone already in the cast',
                'ACTION 2: Cross a meaningful physical distance or obstacle',
                'ACTION 3: Pause for a reciprocal response and adjust the approach',
                'ACTION 4 + PAYOFF: Complete an agreed reunion action and show the new closeness',
            ],
        },
    ],
    'melodrama': [
        {
            "id": 'visible_disclosure',
            "name": 'The Visible Disclosure',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Physically prepare or retrieve evidence already grounded in the scene',
                'ACTION 2: Present the evidence to the relevant existing person',
                'ACTION 3: React to a concrete response by doing something different (step back, hand something over, stop what they were doing)',
                'ACTION 4 + PAYOFF: Make a costly visible decision and show its emotional result',
            ],
        },
        {
            "id": 'breaking_point',
            "name": 'At the Breaking Point',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Try to carry out an ordinary task under pressure',
                'ACTION 2: A concrete interruption forces a different action',
                'ACTION 3: Abandon, change or finish the task with an observable consequence',
                'ACTION 4 + PAYOFF: Take a decisive physical step that changes the situation',
            ],
        },
        {
            "id": 'costly_departure',
            "name": 'Costly Departure',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Gather or put aside a consequential possession',
                'ACTION 2: Move toward an exit or threshold already present',
                'ACTION 3: Confront a practical reason to stay through a new action',
                'ACTION 4 + PAYOFF: Leave or remain by a deliberate final action with lasting consequence',
            ],
        },
        {
            "id": 'reconciliation_attempt',
            "name": 'Attempted Reconciliation',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Approach an existing source conflict in physical space',
                'ACTION 2: Offer a tangible repair, apology gesture or practical help',
                'ACTION 3: Adapt when the other party responds or does not respond',
                'ACTION 4 + PAYOFF: Finish with a visible agreement, boundary or separation',
            ],
        },
    ],
    'slice_of_life': [
        {
            "id": 'task_in_motion',
            "name": 'Everyday Task in Motion',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Start an actual everyday task using available objects',
                'ACTION 2: Start a new phase of the task, with other tools or in another spot, for a practical reason',
                'ACTION 3: Handle a realistic interruption or complication through action',
                'ACTION 4 + PAYOFF: Finish or hand off the task with a visible new arrangement',
            ],
        },
        {
            "id": 'room_reset',
            "name": 'Rearranging a Space',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Identify a useful change within the established location',
                'ACTION 2: Move or sort an already present item within physical reach',
                'ACTION 3: Carry out the second stage of the rearrangement (a heavier item, a higher spot, another corner)',
                'ACTION 4 + PAYOFF: Complete the rearrangement and settle into its practical result',
            ],
        },
        {
            "id": 'skill_attempt',
            "name": 'Learning by Doing',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Try a concrete skill in an observable way',
                'ACTION 2: Change the technique after seeing the result of the first attempt',
                'ACTION 3: Try a more demanding iteration using consistent props',
                'ACTION 4 + PAYOFF: Demonstrate earned progress or limitation with a finishing action',
            ],
        },
        {
            "id": 'small_errand',
            "name": 'A Small Errand',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Set out from the initial pose toward a reachable task',
                'ACTION 2: Navigate a grounded constraint in the setting',
                'ACTION 3: Carry out the errand through a distinct physical interaction',
                'ACTION 4 + PAYOFF: Return or move onward with an observable completed result',
            ],
        },
    ],
    'action': [
        {
            "id": 'obstacle_breakthrough',
            "name": 'Obstacle Breakthrough',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Launch an active attempt toward a visible objective',
                'ACTION 2: An obstacle forces a different route or tactic',
                'ACTION 3: Execute a distinct decisive maneuver using established surroundings',
                'ACTION 4 + PAYOFF: Reach a new position and show the immediate cost or gain',
            ],
        },
        {
            "id": 'pursuit_shift',
            "name": 'Pursuit and Reversal',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Begin moving along a plausible route',
                'ACTION 2: Alter speed, elevation or path to answer a pursuit obstacle',
                'ACTION 3: Take a committed evasive or intercepting maneuver',
                'ACTION 4 + PAYOFF: End the chase with a decisive result (caught, escaped or cornered) and show where everyone ends up',
            ],
        },
        {
            "id": 'defensive_counter',
            "name": 'Defend and Counter',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Take cover, or move to where an attack can be met',
                'ACTION 2: Block or evade a grounded incoming action',
                'ACTION 3: Answer the attack with a countermeasure that uses the setting',
                'ACTION 4 + PAYOFF: Complete the counter or withdrawal with a tangible result',
            ],
        },
        {
            "id": 'precision_rescue',
            "name": 'Timed Rescue',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Position for a practical rescue using available access',
                'ACTION 2: Remove or stabilize an immediate physical obstacle',
                'ACTION 3: Secure the subject by taking hold of them or of their support, and bring them to safety',
                'ACTION 4 + PAYOFF: Complete the rescue action and show who is where afterward',
            ],
        },
    ],
    'mystery_thriller': [
        {
            "id": 'trace_in_space',
            "name": 'Following a Trace',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Move toward an observable clue already supported by the scene',
                'ACTION 2: Examine it through a concrete close interaction',
                'ACTION 3: Test the clue against another grounded location or object',
                'ACTION 4 + PAYOFF: Act on a new physical finding that changes the investigation',
            ],
        },
        {
            "id": 'hidden_access',
            "name": 'Finding Access',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Investigate an established barrier or inaccessible area',
                'ACTION 2: Manipulate a grounded access point without inventing a device',
                'ACTION 3: Get into, or look into, the newly reachable part',
                'ACTION 4 + PAYOFF: Reveal an actionable finding with a visible physical consequence',
            ],
        },
        {
            "id": 'evidence_switch',
            "name": 'Evidence Reconsidered',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Collect or examine an available piece of evidence',
                'ACTION 2: Compare it physically with a second grounded detail',
                'ACTION 3: Change investigative tactic based on a contradiction',
                'ACTION 4 + PAYOFF: Secure or reveal the consequential evidence through action',
            ],
        },
        {
            "id": 'watch_to_intercept',
            "name": 'Watch to Intercept',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Go to a place from which to watch, with a purpose',
                'ACTION 2: Follow a meaningful movement or change in the existing scene',
                'ACTION 3: Go and check what cannot be seen from here, without inventing what is there',
                'ACTION 4 + PAYOFF: Make a concrete intercepting or investigative decision',
            ],
        },
    ],
    'fantasy': [
        {
            "id": 'magic_with_cost',
            "name": 'Magic Has a Cost',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Interact with an established magical source or phenomenon',
                'ACTION 2: Perform a clear magical test with an observable physical effect',
                'ACTION 3: Adapt movement or tactics when a rule or cost becomes visible',
                'ACTION 4 + PAYOFF: Commit to an action that changes the physical situation and shows the price',
            ],
        },
        {
            "id": 'crossing_worlds',
            "name": 'Crossing a Threshold',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Approach a grounded supernatural boundary',
                'ACTION 2: Physically interact with the threshold or its rule',
                'ACTION 3: Cross the threshold or pull back from it',
                'ACTION 4 + PAYOFF: Act within the new conditions and show a concrete discovery',
            ],
        },
        {
            "id": 'creature_contact',
            "name": 'Encounter with a Creature',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: React physically to an established extraordinary creature',
                'ACTION 2: Change distance or shelter in response to creature behavior',
                'ACTION 3: Attempt a meaningful interaction or escape without inventing contact',
                'ACTION 4 + PAYOFF: End with altered positions and a visible consequence',
            ],
        },
        {
            "id": 'enchanted_object',
            "name": 'The Enchanted Object',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Handle or approach an established enchanted object',
                'ACTION 2: Trigger and observe a physical transformation or effect',
                'ACTION 3: Deal with the consequence (catch, hold, shield, drop or move something)',
                'ACTION 4 + PAYOFF: Use or relinquish the object in a decisive new state',
            ],
        },
    ],
    'sci_fi': [
        {
            "id": 'device_procedure',
            "name": 'Device Under Test',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Set up a source-grounded device or interface for a real task',
                'ACTION 2: Operate it and respond to a visible output',
                'ACTION 3: Change the procedure, or go to another part of the device, to deal with a specific malfunction',
                'ACTION 4 + PAYOFF: Complete a second operation with a concrete result',
            ],
        },
        {
            "id": 'zero_g_problem',
            "name": 'Unusual Physics',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Move in response to a clearly established physical constraint',
                'ACTION 2: Use a real nearby surface to push off from, hold or anchor against',
                'ACTION 3: Carry out a task while maintaining plausible physical mechanics',
                'ACTION 4 + PAYOFF: Secure the result of the task and show the consequence',
            ],
        },
        {
            "id": 'containment_failure',
            "name": 'Containment Failure',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Begin a maintenance or inspection action',
                'ACTION 2: A source-compatible failure forces the character to act or move at once',
                'ACTION 3: Perform a bounded containment or isolation action',
                'ACTION 4 + PAYOFF: Resolve or fail to resolve the hazard with a visible changed environment',
            ],
        },
        {
            "id": 'signal_response',
            "name": 'Answering a Signal',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Approach and inspect an established signal source',
                'ACTION 2: Interact with a grounded control or record',
                'ACTION 3: Change the procedure to test a hypothesis',
                'ACTION 4 + PAYOFF: Send, stop or verify a response through a visible action',
            ],
        },
    ],
    'dark_romance': [
        {
            "id": 'boundary_asserted',
            "name": 'A Boundary Asserted',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Approach or withdraw from an emotionally loaded interaction',
                'ACTION 2: A concrete action exposes an interpersonal boundary',
                'ACTION 3: Respect or reinforce the stated boundary through an action (step back, put something down, stop)',
                'ACTION 4 + PAYOFF: Choose a final action showing whether trust survives',
            ],
        },
        {
            "id": 'separation_choice',
            "name": 'The Difficult Separation',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Physically prepare to leave or create distance',
                'ACTION 2: A grounded interruption complicates the departure',
                'ACTION 3: Act on the conflict without denying the other person agency',
                'ACTION 4 + PAYOFF: Leave, remain or establish new terms through an observable choice',
            ],
        },
        {
            "id": 'trust_reversal',
            "name": 'Trust Reconsidered',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Handle a concrete object or situation tied to a doubt',
                'ACTION 2: Confront the relevant party through a grounded interaction',
                'ACTION 3: Change stance or distance in response to a meaningful answer',
                'ACTION 4 + PAYOFF: Take an action that visibly repairs or ends trust',
            ],
        },
        {
            "id": 'dangerous_attraction',
            "name": 'Attraction and Restraint',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Make a deliberate movement toward an existing adult connection',
                'ACTION 2: A credible source-grounded risk interrupts the approach',
                'ACTION 3: Choose to respect an explicit limit or step back',
                'ACTION 4 + PAYOFF: Resolve through a visible choice with psychological cost',
            ],
        },
    ],
    'horror': [
        {
            "id": 'escape_geometry',
            "name": 'Escape Through Space',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: React to a grounded threat with a decisive move (run, hide, shut or block something)',
                'ACTION 2: Try a plausible exit or protective route',
                'ACTION 3: Choose a different route or hiding place when the danger closes in',
                'ACTION 4 + PAYOFF: Reach or lose shelter through an observable outcome',
            ],
        },
        {
            "id": 'approaching_anomaly',
            "name": 'Approaching the Anomaly',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Move to examine an established unsettling detail',
                'ACTION 2: Interact with a reachable trace or environmental change',
                'ACTION 3: Recoil, shield or relocate after a concrete response',
                'ACTION 4 + PAYOFF: Act decisively to contain, avoid or confront the anomaly',
            ],
        },
        {
            "id": 'failing_shelter',
            "name": 'Shelter Fails',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Strengthen or use an existing physical refuge',
                'ACTION 2: An intrusion forces a different way of protecting themselves',
                'ACTION 3: Move to a second refuge or try another way to hold out',
                'ACTION 4 + PAYOFF: Escape or remain trapped with a visible physical result',
            ],
        },
        {
            "id": 'survival_choice',
            "name": 'One Chance to Survive',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Secure an available object or route under threat',
                'ACTION 2: A practical failure forces altered movement',
                'ACTION 3: Choose between two grounded physical actions and commit',
                'ACTION 4 + PAYOFF: Complete the survival attempt with a tangible consequence',
            ],
        },
    ],
    'erotic': [
        {
            "id": 'static_action_erotic',
            "name": 'Bold Action and Presence',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Do something, for a reason found in the scene, that changes the scene or an object in it (leave the seat, reach for, open or use something)',
                'ACTION 2: Because of that, move to another spot or take a clearly different position and do something there',
                'ACTION 3: Change a piece of clothing or the state of an object through a real action, preserving the other states',
                'ACTION 4 + PAYOFF: Finish with a bold, adult, non-explicit action, show its result and the emotional payoff',
            ],
        },
        {
            "id": 'wardrobe_transition',
            "name": 'Private Wardrobe Transition',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Move to begin a private, voluntary clothing-related task grounded in the source',
                'ACTION 2: Adjust or remove a specified garment in a clear non-graphic action, keeping other layers unchanged',
                'ACTION 3: Continue the task, or check how the garment now looks or sits',
                'ACTION 4 + PAYOFF: Finish the task with a final action and a self-directed emotional result; preserve the final clothing state',
            ],
        },
        {
            "id": 'chosen_closeness_static',
            "name": 'Chosen Closeness in Motion',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Move toward an existing consenting adult partner or toward an independent solo task',
                'ACTION 2: Offer or respond to a concrete gesture, establishing boundaries and a changed position',
                'ACTION 3: Carry out a mutually chosen non-graphic intimate gesture or a meaningful solo action',
                'ACTION 4 + PAYOFF: Finish with a final action and an emotional change earned by what happened',
            ],
        },
        {
            "id": 'confident_transformation',
            "name": 'Confident Transformation',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Do something, for a clear reason, that begins to change how the character presents themselves',
                'ACTION 2: Interact with an established garment, accessory or object through a real action',
                'ACTION 3: Move to a new spot or position because of the previous action and do something there',
                'ACTION 4 + PAYOFF: Conclude with a confident physical choice and its emotional consequence, without forcing nudity',
            ],
        },
    ],
    'dark_erotic': [
        {
            "id": 'private_risk',
            "name": 'Privacy and Risk',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Move toward a plausible private action within the established scene',
                'ACTION 2: Handle a source-grounded interruption or privacy concern by acting on it',
                'ACTION 3: Make an adult voluntary non-graphic clothing, proximity or boundary-related choice',
                'ACTION 4 + PAYOFF: Finish with a distinct physical outcome and the emotional cost of secrecy',
            ],
        },
        {
            "id": 'boundary_in_motion',
            "name": 'Boundaries in Motion',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Act to make a personal limit physically clear',
                'ACTION 2: Change distance or contact based on a willing adult response',
                'ACTION 3: Choose a different purposeful action that respects the boundary',
                'ACTION 4 + PAYOFF: Conclude with a grounded shift in power or trust, never portraying coercion as consent',
            ],
        },
        {
            "id": 'concealed_identity',
            "name": 'The Concealed Detail',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Do something about an already-grounded private personal detail',
                'ACTION 2: Handle a garment or object to protect or reveal that detail, without graphic description',
                'ACTION 3: Respond to the practical consequences with a distinct action',
                'ACTION 4 + PAYOFF: Make a visible final choice about concealment and emotional exposure',
            ],
        },
        {
            "id": 'temptation_with_cost',
            "name": 'A Choice with Cost',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Begin a meaningful adult choice through a physical action',
                'ACTION 2: Bring the real-world consequence into view by interacting with the setting',
                'ACTION 3: Move into or out of an optional, freely chosen non-graphic encounter',
                'ACTION 4 + PAYOFF: Finish with a distinct physical outcome and the emotional price of that choice',
            ],
        },
    ],
    'drama': [
        {
            "id": 'decision_made_visible',
            "name": 'Decision Made Visible',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Try a practical action toward a source-supported goal',
                'ACTION 2: An obstacle forces a different method or a different use of objects',
                'ACTION 3: Make a consequential choice through a distinct visible action',
                'ACTION 4 + PAYOFF: Carry the choice through and show the changed circumstances',
            ],
        },
        {
            "id": 'repair_attempt',
            "name": 'Attempted Repair',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Begin fixing an existing practical or relational problem',
                'ACTION 2: Adjust approach after an observed setback',
                'ACTION 3: Make a tangible effort that takes more than before (more strength, another tool, help from another person)',
                'ACTION 4 + PAYOFF: Complete or abandon the repair and show its effect',
            ],
        },
        {
            "id": 'confrontation_route',
            "name": 'Confrontation in Space',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Move to address a grounded conflict directly',
                'ACTION 2: Use an actual object, location or interpersonal boundary in the exchange',
                'ACTION 3: React to a concrete reversal with a changed action',
                'ACTION 4 + PAYOFF: Resolve or escalate the conflict through a physical decision',
            ],
        },
        {
            "id": 'sacrifice_in_action',
            "name": 'A Tangible Sacrifice',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Act to preserve an important source-grounded priority',
                'ACTION 2: A concrete conflict makes that course harder',
                'ACTION 3: Give up or redirect something through an observable action',
                'ACTION 4 + PAYOFF: Complete the chosen sacrifice and show its consequence',
            ],
        },
    ],
    'comedy': [
        {
            "id": 'physical_mixup',
            "name": 'The Physical Mix-up',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Begin an earnest ordinary action with available props',
                'ACTION 2: A grounded misunderstanding leads to the wrong physical move',
                'ACTION 3: An attempted recovery makes the mess worse',
                'ACTION 4 + PAYOFF: Resolve with a visible reversal and a funny result',
            ],
        },
        {
            "id": 'overcomplicated_task',
            "name": 'Too Much Effort',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Set out to solve a tiny source-grounded task',
                'ACTION 2: Try an unnecessarily elaborate but physically plausible maneuver',
                'ACTION 3: Adapt after the maneuver produces a clear complication',
                'ACTION 4 + PAYOFF: Finish with a simpler action that exposes the absurdity',
            ],
        },
        {
            "id": 'awkward_positions',
            "name": 'The Awkward Position',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Do something for an ordinary goal that leaves the character in an awkward but plausible spot',
                'ACTION 2: A social or spatial complication forces a bigger adjustment',
                'ACTION 3: Try a new recovery action without teleporting props or limbs',
                'ACTION 4 + PAYOFF: End in a visibly different and comic yet coherent situation',
            ],
        },
        {
            "id": 'mistimed_entrance',
            "name": 'The Mistimed Entrance',
            "steps": [
                'ENTRY: Reproduce the canonical SOURCE moment precisely: visible cast, place, initial body pose and support, hands and legs, held objects, clothing and spatial relationships. Start AND END this step in the source pose; no transition yet.',
                'ACTION 1: Begin a practical approach or entrance using established space',
                'ACTION 2: A surprising interruption forces an abrupt changed movement',
                'ACTION 3: Attempt a corrective action that compounds the timing problem',
                'ACTION 4 + PAYOFF: Land a comic payoff through a final action and its visible result',
            ],
        },
    ],
}

# Integrity and discoverability: no runtime source interpretation in Python.
assert len(_STATIC_GENRE_GOALS) == len(_STATIC_GENRE_ARCS) == 13
assert all(len(arcs) == 4 and all(len(arc["steps"]) == 5 for arc in arcs) for arcs in _STATIC_GENRE_ARCS.values())
assert len({arc["id"] for arcs in _STATIC_GENRE_ARCS.values() for arc in arcs}) == 52

def format_static_genre_arc_options(genre_key: str) -> str:
    """Lookup/format only. LLM chooses among four source-compatible paths."""
    arcs = _STATIC_GENRE_ARCS.get(genre_key, ())
    return "\n".join(f"{chr(65+i)}. {a['name']} [{a['id']}]: " + " -> ".join(a["steps"]) for i, a in enumerate(arcs))
