"""Groom v4 curated narrative goals and arcs.

Author-defined creative templates. Python only looks up and serializes these data.
The LLM chooses/adapts arcs to the source cast and setting.
"""

_GENRE_GOALS = {
    "romance": "Develop affection, trust, and choices about meaningful emotional connection; use the existing cast rather than inventing a partner.",
    "melodrama": "Bring emotionally consequential desires, revelations, sacrifices, and repercussions into visible action.",
    "slice_of_life": "Make ordinary activities reveal small but meaningful changes in habits, understanding, or daily circumstances.",
    "action": "Drive concrete objectives through physical obstacles, tactical choices, changing advantage, and consequences.",
    "mystery_thriller": "Turn uncertainty into investigation, reinterpretation of evidence, escalating stakes, and a consequential discovery.",
    "fantasy": "Make the world's extraordinary rules affect decisions, costs, discoveries, and the story's outcome.",
    "sci_fi": "Use speculative technology or science to generate testable problems, difficult tradeoffs, and human consequences.",
    "dark_romance": "Explore intense attachment, risk, boundaries, and difficult emotional choices without romanticizing coercion.",
    "horror": "Make an unsettling anomaly evolve into tangible danger or dread that forces response and leaves consequences.",
    "erotic": "Develop an event-driven adult erotic story: make attraction, bodily vulnerability, chosen closeness, or a meaningful non-graphic nudity event change what characters do, decide, or understand. Show concrete actions and their consequences rather than prolonged suggestive atmosphere. When clothing changes or nudity occurs, state what actually happens and preserve its resulting state until another event changes it. Retain such events already established in SOURCE. Do not force the same undressing sequence into every story; honor explicit source constraints, adult identities, consent, and agency; avoid graphic sexual detail.",
    "dark_erotic": "Develop an event-driven adult dark-erotic story in which bodily vulnerability, chosen closeness, or meaningful non-graphic nudity intersects with secrecy, psychological tension, risk, boundaries, and consequences. Make concrete events change the situation instead of substituting suggestive atmosphere for plot. When clothing changes or nudity occurs, show the actual transition, retain its state across subsequent events, and preserve any such event established in SOURCE. Vary the narrative pathway rather than forcing one undressing template; keep adult identities and agency intact and never frame coercion as consent or supply graphic sexual detail.",
    "drama": "Drive realistic dilemmas through choices, competing needs, and the tangible effect of decisions.",
    "comedy": "Turn an expectation or attempt into escalating incongruity and a consequential, character-driven comic reversal.",
}

# Four genuinely different paths per genre. Steps are narrative functions,
# not instructions to add new people, places, or mandatory plot props.
_GENRE_ARCS = {
    "romance": [
        {"id": "recognition", "name": "Recognizing Affection", "steps": ["Establish an existing bond or longing", "A familiar detail gains new emotional meaning", "A choice exposes vulnerability", "The character acts on or resists that recognition", "Show what changes in the bond or self-understanding"]},
        {"id": "earned_trust", "name": "Trust Earned", "steps": ["Establish a guarded emotional position", "A practical situation tests reliability", "A small action builds or weakens trust", "A harder choice tests the new understanding", "Show a believable shift in emotional openness"]},
        {"id": "competing_paths", "name": "Love and a Competing Goal", "steps": ["Establish an emotional attachment and another priority", "The two priorities begin to conflict", "An attempted compromise creates a cost", "Make a choice without erasing either need", "Show the new terms of the attachment or longing"]},
        {"id": "reassessment", "name": "Reassessing a Bond", "steps": ["Establish an assumption about love or a relationship", "A new observation challenges that assumption", "An old incident takes on a different meaning", "Choose whether to reconnect, change course, or let go", "Show the resulting emotional direction"]},
    ],
    "melodrama": [
        {"id": "withheld_truth", "name": "The Withheld Truth", "steps": ["Establish something emotionally important but unspoken", "A circumstance makes concealment harder", "A partial disclosure shifts expectations", "The full significance demands a response", "Show the emotional consequences of knowing"]},
        {"id": "costly_devotion", "name": "The Cost of Devotion", "steps": ["Establish a deeply held commitment", "An incompatible need appears", "Trying to satisfy both demands a sacrifice", "Choose what to keep and what to risk", "Show the personal cost beyond the immediate decision"]},
        {"id": "misread_signals", "name": "A Painful Misreading", "steps": ["Establish an emotionally charged expectation", "An ambiguous action is interpreted personally", "A reaction deepens the misunderstanding", "A concrete fact reframes the incident", "Show whether trust can recover"]},
        {"id": "last_straw", "name": "The Last Straw", "steps": ["Show a burden that has accumulated", "A modest event lands with disproportionate force", "An attempt to hold things together fails", "A decisive release or refusal changes the situation", "Show the quieter emotional fallout"]},
    ],
    "slice_of_life": [
        {"id": "routine_shift", "name": "A Changed Routine", "steps": ["Begin in a specific everyday task", "A small practical disruption interrupts it", "Adjust the task in a character-revealing way", "Discover an unexpectedly useful or pleasant outcome", "Return to the day with a modest change"]},
        {"id": "craft_progress", "name": "Practice and Progress", "steps": ["Establish a familiar skill or chore", "A small imperfection becomes noticeable", "Try one concrete adjustment", "Observe a partial success or new difficulty", "End with a plausible next habit"]},
        {"id": "ordinary_discovery", "name": "An Ordinary Discovery", "steps": ["Follow a familiar route or activity", "Notice an overlooked detail", "Investigate through simple everyday actions", "Let the discovery alter a small decision", "Show the day continuing with fresh perspective"]},
        {"id": "personal_ritual", "name": "The Meaning of a Ritual", "steps": ["Show a recurring personal ritual", "One step feels different today", "A memory or observation reframes its purpose", "Choose to continue or alter the ritual", "Close on a specific visible change"]},
    ],
    "action": [
        {"id": "lost_advantage", "name": "Advantage Lost", "steps": ["Establish an immediate physical objective", "Attempt a direct route", "An obstacle removes the expected advantage", "Improvise a more costly method", "Resolve the immediate objective with a changed situation"]},
        {"id": "time_pressure", "name": "Against the Clock", "steps": ["Introduce a concrete deadline or closing opportunity", "Make tangible progress toward it", "A delay forces a tradeoff", "Commit to a risky but purposeful choice", "Show what is achieved or lost when time runs out"]},
        {"id": "terrain_shift", "name": "Changing Terrain", "steps": ["Establish the objective within a physical environment", "Use the setting to gain ground", "Conditions shift and invalidate the initial approach", "Change tactics using what remains available", "End with a new physical position and consequence"]},
        {"id": "controlled_retreat", "name": "The Better Retreat", "steps": ["Begin with a direct confrontation or difficult task", "An attempted advance reveals a steep cost", "Reassess the real objective", "Withdraw or redirect with deliberate tactics", "Show the valuable gain and unresolved risk"]},
    ],
    "mystery_thriller": [
        {"id": "contradictory_trace", "name": "The Contradictory Trace", "steps": ["Present an observable discrepancy", "Form the most obvious explanation", "Find a detail that contradicts it", "Test a different interpretation", "Reveal a consequence and an actionable new question"]},
        {"id": "missing_interval", "name": "The Missing Interval", "steps": ["Establish a gap in an otherwise clear sequence", "Reconstruct what is known", "Identify the detail that does not fit", "Revisit the gap from a new angle", "Discover why the missing interval matters"]},
        {"id": "false_certainty", "name": "A Convincing Wrong Answer", "steps": ["Begin with a plausible explanation", "Gather facts that appear to confirm it", "Spot one overlooked inconsistency", "Reverse the working hypothesis", "Show the stakes of the revised conclusion"]},
        {"id": "narrowing_window", "name": "The Narrowing Window", "steps": ["Notice a troubling unanswered question", "Set a practical way to investigate", "A time or access constraint tightens", "Choose which lead to pursue at a cost", "Gain partial truth before the window closes"]},
    ],
    "fantasy": [
        {"id": "rule_discovered", "name": "A Rule of Magic", "steps": ["Encounter an uncanny effect", "Attempt to understand what triggers it", "Test the apparent rule", "Discover a limitation or unexpected cost", "Use the new knowledge to make a consequential choice"]},
        {"id": "price_of_power", "name": "Power Has a Price", "steps": ["Establish a desired outcome that magic might provide", "Discover a tempting supernatural means", "Experience a smaller side effect", "Choose whether the full price is acceptable", "Show the lasting consequence of the choice"]},
        {"id": "threshold", "name": "Crossing a Threshold", "steps": ["Establish a familiar place or way of life", "Find a boundary that defies ordinary rules", "Cross or approach through concrete action", "Adapt to a surprising law of the new situation", "End with a changed understanding or obligation"]},
        {"id": "broken_enchantment", "name": "The Unraveling Enchantment", "steps": ["Show a magical condition everyone takes for granted", "A small sign suggests instability", "An attempt to repair it changes something else", "Choose what can realistically be saved", "Reveal the altered rules of the aftermath"]},
    ],
    "sci_fi": [
        {"id": "unexpected_result", "name": "Unexpected Test Result", "steps": ["Establish an experiment or technical task", "Observe an anomalous result", "Run a targeted check instead of guessing", "Learn a surprising limitation or implication", "Act on the discovery and show its cost"]},
        {"id": "system_tradeoff", "name": "System Tradeoff", "steps": ["Depend on a technology for a concrete goal", "An optimization creates an overlooked problem", "Attempt a technical workaround", "Choose which benefit to sacrifice", "Show the human effect of the system change"]},
        {"id": "signal", "name": "An Unexplained Signal", "steps": ["Detect a pattern where none is expected", "Try to rule out familiar causes", "A second observation alters the pattern", "Make a high-stakes interpretation or response", "Show a consequence that opens new possibilities"]},
        {"id": "human_limit", "name": "Human Limits", "steps": ["Establish a capability enhanced by technology", "A task exposes the limits of the person or machine", "Compensate in a way that raises a new risk", "Choose between performance and wellbeing", "End with a changed definition of success"]},
    ],
    "dark_romance": [
        {"id": "boundary_test", "name": "A Boundary Tested", "steps": ["Establish a compelling but uneasy attachment", "A situation exposes a personal boundary", "Pressure makes the boundary consequential", "Choose whether and how to uphold it", "Show the resulting transformation or fracture of trust"]},
        {"id": "unreliable_promise", "name": "An Unreliable Promise", "steps": ["Introduce a promise carrying emotional risk", "Find evidence that complicates belief", "Take a vulnerable step despite uncertainty", "Confront the cost of misplaced or earned trust", "Show a relationship changed by the truth"]},
        {"id": "possession_or_care", "name": "Possession or Care", "steps": ["Establish intense attachment", "A controlling impulse conflicts with autonomy", "A concrete choice reveals the difference", "A boundary must be respected or enforced", "Show the emotional price and direction ahead"]},
        {"id": "past_shadow", "name": "The Shadow of the Past", "steps": ["Show how an old wound shapes present affection", "A familiar trigger revives fear or suspicion", "An attempted defense harms connection", "Choose between an old pattern and a new response", "Show what remains unresolved and what genuinely changes"]},
    ],
    "horror": [
        {"id": "subtle_wrongness", "name": "Something Slightly Wrong", "steps": ["Begin in an ordinary action", "Notice one impossible or deeply wrong detail", "Attempt a mundane explanation", "The anomaly repeats with greater consequence", "Force a decision that leaves the danger palpable"]},
        {"id": "shrinking_safety", "name": "Safety Shrinks", "steps": ["Establish a place or habit that feels protective", "An intrusion undermines one safeguard", "A second option becomes unreliable", "Take a desperate but reasoned measure", "End with a changed idea of what is safe"]},
        {"id": "uncertain_senses", "name": "Can I Trust My Senses?", "steps": ["Encounter a disturbing sensory impression", "Check it against physical evidence", "New evidence supports mutually exclusive explanations", "Act despite uncertainty", "Reveal a tangible cost without resolving every question"]},
        {"id": "the_price_of_escape", "name": "The Price of Escape", "steps": ["Reveal an imminent disturbing threat", "Attempt the obvious escape", "Learn the escape carries a hidden cost", "Choose between two frightening consequences", "Show the immediate outcome and its lingering fear"]},
    ],
    "erotic": [
        {"id": "private_transition", "name": "Private Transition", "steps": ["Establish an adult character handling a specific private task", "A practical choice changes the situation and calls for privacy", "Show any actual clothing or exposure transition clearly and matter-of-factly", "An interruption, realization, or decision gives the vulnerable moment narrative significance", "Show the response and maintain the resulting physical state through the next event"]},
        {"id": "chosen_closeness", "name": "Chosen Closeness", "steps": ["Establish a wish for closeness among existing adults", "An interaction makes that wish actionable", "Characters express a boundary or make a mutual choice", "Show a concrete, consensual moment of intimacy or bodily vulnerability without graphic detail", "Show how their behavior or relationship changes afterward"]},
        {"id": "anticipation_release", "name": "Anticipation and Release", "steps": ["Establish anticipation tied to a concrete occasion", "A practical preparation changes what the character can do", "A setback or hesitation requires a real choice", "Resolve the anticipation in an actual private encounter, action, or exposure rather than a mere promise", "Show what changes afterward, without repeating descriptions of sensation"]},
        {"id": "self_acceptance", "name": "Comfort in One's Body", "steps": ["Establish an adult discomfort or self-consciousness", "A private task makes the feeling consequential", "A deliberate action changes the situation", "Show a matter-of-fact bodily-vulnerability or non-graphic exposure moment where appropriate", "Show earned confidence or a changed routine, preserving any clothing-state transition"]},
    ],
    "dark_erotic": [
        {"id": "concealed_longing", "name": "Concealed Longing", "steps": ["Establish a private adult longing with a credible reason for secrecy", "An event threatens to reveal it", "An adult chooses how to respond within their boundaries", "Show a concrete private action, chosen closeness, or non-graphic exposure that changes what is at stake", "Follow the consequence for secrecy, trust, or personal safety"]},
        {"id": "dangerous_disclosure", "name": "Dangerous Disclosure", "steps": ["Establish an intimate truth and what its disclosure could cost", "A tangible clue or interruption raises the risk", "A character acts to protect, reveal, or reconsider it", "A private moment of bodily vulnerability or a consequential encounter makes the decision real", "Show its effect on safety, trust, or self-understanding"]},
        {"id": "power_rebalanced", "name": "Power Rebalanced", "steps": ["Establish unequal expectations between adults", "A private interaction exposes the imbalance", "A character asserts a boundary or withholds agreement", "A concrete action redistributes control, with intimacy or vulnerability only where freely chosen", "Show the consequence without equating pressure with consent"]},
        {"id": "temptation_cost", "name": "The Cost of Temptation", "steps": ["Establish an adult wish carrying a believable cost", "A practical opportunity brings it within reach", "A decision pits desire against a real consequence", "Show the resulting concrete encounter, private act, or chosen vulnerability rather than stopping at anticipation", "Reveal the cost or relief through the next event"]},
    ],
    "drama": [
        {"id": "personal_conflict", "name": "Personal Conflict", "steps": ["Establish a concrete personal goal", "Introduce an obstacle with real consequences", "A first attempt increases pressure", "Make a difficult choice between costs", "Show the outcome and the changed circumstances"]},
        {"id": "difficult_truth", "name": "Difficult Truth", "steps": ["Begin with a workable assumption", "Encounter evidence that challenges it", "Avoidance or denial creates a cost", "Accept or reject the truth through a concrete action", "Show how life changes because of that action"]},
        {"id": "competing_duties", "name": "Competing Duties", "steps": ["Establish two obligations that both matter", "A practical event makes them incompatible", "Try to negotiate time, resources, or responsibility", "Choose which promise can be kept", "Show the lasting impact on priorities"]},
        {"id": "setback_recovery", "name": "Setback and Recovery", "steps": ["Establish effort already invested in a goal", "A setback removes a valued option", "An initial response fails or proves insufficient", "Find a more honest or limited way forward", "End on earned progress rather than easy victory"]},
    ],
    "comedy": [
        {"id": "misinterpretation", "name": "The Misinterpretation", "steps": ["Establish an ordinary intention", "A harmless clue gets misread", "An attempt to correct it produces a funnier misunderstanding", "A concrete revelation changes the interpretation", "Close with a character-based comic consequence"]},
        {"id": "overengineered", "name": "The Overcomplicated Fix", "steps": ["Introduce a tiny practical problem", "Choose an absurdly elaborate solution", "Each step creates a more visible complication", "A simple overlooked action resolves or flips the problem", "Land a payoff revealing the character's blind spot"]},
        {"id": "awkward_recovery", "name": "Recovering from Embarrassment", "steps": ["Establish a normal social or private goal", "An innocent mistake creates embarrassment", "An effort to appear composed makes it worse", "An unexpected honest response changes the situation", "Finish with a warm or sharp comic reversal"]},
        {"id": "expectation_flip", "name": "Expectation Reversed", "steps": ["Set up a confident prediction", "Build evidence that seems to confirm it", "Introduce a detail that points the other way", "Reveal the mistaken assumption through action", "End with a surprising but earned final beat"]},
    ],
}

assert len(_GENRE_ARCS) == len(_GENRE_GOALS) == 13
assert all(len(arcs) == 4 and all(len(a['steps']) == 5 for a in arcs) for arcs in _GENRE_ARCS.values())


def format_genre_arc_options(genre_key: str) -> str:
    """Lookup/format only; does not interpret SOURCE or choose an arc."""
    items = _GENRE_ARCS.get(genre_key, ())
    return "\n".join(
        f"{chr(65 + i)}. {arc['name']} [{arc['id']}]: " + " -> ".join(arc['steps'])
        for i, arc in enumerate(items)
    )
