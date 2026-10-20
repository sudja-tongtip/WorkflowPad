"""The A1 Static / Novel and A2 request wording must ask for a story a reader can follow.

Run:  python -m story_generator.tests.test_a1_story_sense

(Rewritten for the language-neutral A1/A2 fix: the checks follow the CURRENT design - events with action, response and consequence; the pose as the visible result; one beat rule.
The structure / multilingual checks are in test_a1_a2_language_neutral.py.)
"""
from story_generator.a1_static_conversation import _SYSTEM_PROMPT, _req_event_step, _req_events, _req_macros
from story_generator.llm_engine_a2 import _A2_SYSTEM_PROMPT, _build_one_macro_user_prompt
from story_generator.a1_novel_conversation import _NOVEL_SYSTEM_PROMPT
from story_generator.a1_novel_conversation import _req_event_step as _novel_step
from story_generator.a1_static_genre_arcs import _STATIC_GLOBAL_RULES

_fails: list = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


_ARC = {
    "genre": "drama",
    "goal": "Represent conflict through concrete acts.",
    "id": "decision_made_visible",
    "name": "Decision Made Visible",
    "rules": _STATIC_GLOBAL_RULES,
    "steps": [
        "ENTRY: reproduce the source.",
        "ACTION 1: try a practical action.",
        "ACTION 2: an obstacle forces a different method.",
        "ACTION 3: make a consequential choice.",
        "ACTION 4 + PAYOFF: show the changed circumstances.",
    ],
}


def main():
    check("static system: events follow from each other", "because of the event before it" in _SYSTEM_PROMPT)
    check("static system: the pose is the result of the event", "visible result" in _SYSTEM_PROMPT)
    check("static rules: the arc is adapted to what the source has", "same job with what the source" in _STATIC_GLOBAL_RULES)
    check("static rules: no partner is invented for a solo source", "solo source" in _STATIC_GLOBAL_RULES)

    opening = _req_event_step(_ARC, 1, ["Mina"], 1, desc_lang="en")
    check("static opening stays on the source", "Do not begin the next event" in opening)
    check("static opening does not ask for a new pose", "clearly different" not in opening)

    later = _req_event_step(_ARC, 2, ["Mina"], 1, desc_lang="en")
    check("static later event asks for action, response and consequence", "reaction" in later and "consequence" in later)
    check("static later event starts where the previous ended", "exact situation where the previous paragraph ended" in later)
    check("static later event is not limited to two sentences", "Exactly two sentences" not in later and "sentence" not in later.lower().replace("do not write only a pose", ""))
    check("static later event adapts the arc to the source", "what it has" in later and "do not add a person or an object the source does not support" in later)
    check("static later event is not tied to one picture", all(w not in later.lower() for w in ("maid", "kitten", "laptop", "desk", "tray", "glue", "rope")))

    last = _req_event_step(_ARC, 5, ["Mina"], 1, desc_lang="en")
    check("static last event brings the story to a visible result", "result the reader can see" in last)

    batch = _req_events(_ARC, ["Mina"], 1, desc_lang="en", vn=False)
    check("static batch refuses a pose-only paragraph", "only changes a pose" in batch)
    check("static batch is one story", "events of one short story" in batch)

    macros = _req_macros(1, 5, desc_lang="en", vn=False)
    check("static macros keep the plot", "Do not shorten an event down to a pose" in macros)
    check("static macros keep the source pose on E1", "E1 keeps the source's starting pose exactly" in macros)

    check("novel system does not rewrite the plot", "do not write a new plot" in _NOVEL_SYSTEM_PROMPT.lower())
    novel = _novel_step(_ARC, 3, ["Mina", "Jon"], desc_lang="en")
    check("novel passage stays in story order", "in the story's own order" in novel)
    check("novel arc is emphasis only", "emphasis only" in novel)
    check("novel does not hunt for a matching part", "the part that fits this step" not in novel)
    check("novel forbids invention", "Never add a character" in novel)
    end = _novel_step(_ARC, 5, ["Mina"], desc_lang="en")
    check("novel last passage keeps the story ending", "actual final event" in end)

    a2 = _build_one_macro_user_prompt(
        {"character_sheet": "June", "world_sheet": "", "locations": "", "important_objects": "", "continuity_notes": ""},
        {"id": "E2", "content": "June sets the tray down and tells him the rolls burned."},
        mode="sequential", desc_lang="en")
    check("a2 keeps a decision with its action", "same beat" in a2)
    check("a2 does not copy reference clothing", "Do not copy clothing" in a2)
    check("a2 keeps both results in a sentence", "keep both" in a2)
    check("a2 keeps a frozen moment as one beat", "return one beat" in a2)
    check("a2 keeps a look inside the action", "not a beat of its own" in a2)
    check("a2 beat is a drawable state or action phase", "drawable state or action phase" in a2)
    check("a2 does not count seconds or list English connectives", "second" not in a2 and "then, after, or next" not in a2)
    check("a2 system stays short", len(_A2_SYSTEM_PROMPT) < 500)

    print("ALL PASS" if not _fails else f"{len(_fails)} FAILED")
    return 0 if not _fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
