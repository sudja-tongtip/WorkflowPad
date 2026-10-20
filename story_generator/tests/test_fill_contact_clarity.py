"""IDENTITY LOCK ต้องเป็นรูปร่าง ไม่ใช่ท่าทาง และคำสั่ง fill ต้องไม่ยาวขึ้นโดยไม่จำเป็น

รันด้วย:  python -m story_generator.tests.test_fill_contact_clarity
"""
from .. import llm_engine_fill as F
from ..fill_conversation import CONVERSATION_RULES, attach_sheets, shot_request, system_text
from ..llm_engine_fill import _FILL_SYSTEM_PROMPT, _output_shape_note

_fails: list = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


_note = _output_shape_note(True)
_lock = "Nothing about pose, action, place, or contact."
for _label, _text in (("system", _FILL_SYSTEM_PROMPT), ("conversation", system_text(F)), ("shot note", _note)):
    check(f"{_label} locks body shape only", _lock in _text)
    check(f"{_label} does not ask IDENTITY LOCK for the pose", "current physical state" not in _text.split("IDENTITY LOCK:")[-1][:500] if "IDENTITY LOCK:" in _text else "current physical state" not in _text)

check("the contact lecture was not added back", "whose part touches whose part" not in _FILL_SYSTEM_PROMPT)
for _gone in (
    "Expand that exact moment",
    "Make spatial relationships unmistakable",
    "letting it dominate the description",
    "where the subject sits or stands",
    "Preserve all spatial truth",
):
    check(f"cut: {_gone}", _gone not in _FILL_SYSTEM_PROMPT)
check("pose is not restated outside MAIN", "FRAME LAYOUT and LOCATION LOCK do not restate them." in _FILL_SYSTEM_PROMPT)
check("object lock does not ask for the action", "relation to the person or action" not in _FILL_SYSTEM_PROMPT)
_req = shot_request(F, {"shot": 1, "shot_desc": "x"}, 0, 1, {}, [])
check("the shot request ends at the B shot", _req.rstrip().endswith("CURRENT B SHOT:\n" + F._shot_payload({"shot": 1, "shot_desc": "x"})))
check("the shot request does not name this story's pose", "face down" not in _req and "cushion" not in _req)
check("the system prompt did not grow a pose ban", "never atop" not in _FILL_SYSTEM_PROMPT)
check("illustrator reads the sheets", "You are an illustrator who receives the data and writes one final Krea 2 image prompt." in _FILL_SYSTEM_PROMPT)
check("meaning becomes a complete picture", "Interpret the meaning of the story and of shot_desc" in _FILL_SYSTEM_PROMPT)
check("this story's pose is not mandatory", "face down" not in _FILL_SYSTEM_PROMPT and "cushion" not in _FILL_SYSTEM_PROMPT and "sofa" not in _FILL_SYSTEM_PROMPT)
check("frame template stays position only", "No pose, action, or contact." in _output_shape_note(True))
check("data sheets are not an ACK turn", "PRODUCTION SHEET" not in CONVERSATION_RULES)
check("data sheets are named in the system rules", "DATA SHEETS" in CONVERSATION_RULES)
check("a sheet is attached to the system prompt", attach_sheets("RULES", "CHARACTERS:\nAda").endswith("CHARACTERS:\nAda"))
check("an empty sheet adds nothing", attach_sheets("RULES", "  ") == "RULES")
check(
    "final check is about any body and any support",
    "no extra limb" in _FILL_SYSTEM_PROMPT
    and "Every person in visible_people is inside the frame" in _FILL_SYSTEM_PROMPT
    and "no body part passes through furniture" in _FILL_SYSTEM_PROMPT
    and "Where bodies touch, the contact is drawn at the body parts the shot names" in _FILL_SYSTEM_PROMPT,
)
check("main opens with the view", "MAIN IMAGE DESCRIPTION opens with the view, then one sentence per fact." in _FILL_SYSTEM_PROMPT)
check("each fact is one sentence", "Write each fact once, in concrete words, naming the body parts the shot names." in _FILL_SYSTEM_PROMPT)
check("the camera is chosen from the shot's emotion", "choose one camera from the Emotion words of this shot" in _FILL_SYSTEM_PROMPT and "behind the person who feels it" in _FILL_SYSTEM_PROMPT)
check("a recent camera is not repeated without a visual necessity", "repeats a recent one without a visual necessity" in _FILL_SYSTEM_PROMPT and "the camera may stay" not in _FILL_SYSTEM_PROMPT)
for _banned in ("face down", "cushion", "sofa", "penis", "side of the bed"):
    check(f"not hardcoded: {_banned}", _banned not in _FILL_SYSTEM_PROMPT)

print()
print(f"{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
