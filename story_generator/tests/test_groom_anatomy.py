"""เทสต์ว่า Groom ไม่สร้างความผิดพลาดเรื่องอวัยวะตั้งแต่ตอนเขียน prose

รันด้วย:  python -m story_generator.tests.test_groom_anatomy

ปัญหาเดิม: Fill/Checker/Director/Chat ถูกสอนเรื่องอวัยวะแล้ว (test_anatomy_interaction)
แต่ Groom อยู่ต้นสายและยังไม่ถูกสอน — ชีตบอกว่าแขนทั้งสองข้างเป็นแขนกล groom ขยายฉาก
แล้วเขียน "มือเนื้อ" ทำอย่างที่สาม ของเสียไหลลง Events → Shots → Fill แล้ว Checker
ปลายทางต้องมาซ่อมปัญหาที่ไม่ได้เกิดตรงนั้น

ขอบเขต: เทสต์ระดับ prompt contract — ยืนยันว่าคำสั่งถูกส่งถึงโมเดล ไม่ได้ยืนยันว่าโมเดลทำตาม
"""
import inspect
import re as _re

from .. import llm_engine as L

_fails: list = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


_RULE = L._GROOM_ANATOMY_RULE
_SRC = inspect.getsource(L)
_low = _RULE.lower()

# ── A: กฎครบทุกประเด็นของ brief ────────────────────────────────────────────
check("A body is preserved, action is developed",
      "THE BODY IS NOT YOURS TO CHANGE, ONLY WHAT IT DOES" in _RULE)
check("A only an explicit story event may change a body",
      "only an explicit story event" in _low)
check("A no silent reversal of limb type",
      "turn a mechanical arm back into flesh" in _low
      and "grow back a missing one" in _low
      and "drop a prosthetic" in _low)
check("A no duplicated appendage",
      "duplicate an appendage" in _low)
check("A no anatomy transfer between characters",
      "move one person's body onto another" in _low)
check("A actions must fit the existing body",
      "ACTIONS MUST FIT THE BODY THAT EXISTS" in _RULE)
check("A never invent a limb for an extra action",
      "never invent an extra hand" in _low
      and "has just given her a third" in _low)
check("A resolution list: finish / put down / combine / restage",
      all(w in _low for w in ("finish what it was doing", "put an object down first",
                              "combine actions", "restage the moment")))
check("A the beat survives, the staging changes",
      "the story beat survives" in _low)
check("A sequential reuse of one limb is valid",
      "OVER TIME IS NOT AT ONCE" in _RULE
      and "four actions and still two hands" in _low)
check("A limit applies to simultaneous only",
      "applies only to what happens simultaneously" in _low)
check("A compatible multi-action stays allowed",
      "is one hand, not two" in _low)
check("A prose stays natural",
      "STAY NATURAL" in _RULE and "not a body-position report" in _low)
check("A ownership named only where it matters",
      "count one more than the character has" in _low)
check("A judged against this character",
      "four arms doing four things is right" in _low
      and "impossible for someone with one arm" in _low)
check("A source contradiction is surfaced, not invented away",
      "IF THE SOURCE ITSELF CONTRADICTS" in _RULE
      and "say so in one line" in _low
      and "never resolve it by giving someone a limb they" in _low)

check("A limb map stays internal, never printed",
      "KEEP THE WORKING OUT TO YOURSELF" in _RULE
      and "no list of body parts" in _low)
check("A anatomy repair never softens content",
      "THIS IS NEVER A REASON TO SOFTEN ANYTHING" in _RULE
      and "changes the staging of a moment, never its content" in _low
      and "violence, sex, horror" in _low)

# ── B: generic / ไม่ hard-code ─────────────────────────────────────────────
for _kind in ("mechanical", "missing", "prosthetic", "tail", "wings", "non-human"):
    check(f"B rule names {_kind}", _kind in _low)
check("B rule does not assume everyone has two arms",
      not _re.search(r"\b(every (?:person|character) has two|exactly two arms|max_hands)\b", _low))
check("B rule is not written around the cyberpunk example",
      "cyberpunk" not in _low and "whisky" not in _low)
check("B no groom-side limb regex was introduced",
      not [n for n in dir(L)
           if n.startswith("_GROOM") and isinstance(getattr(getattr(L, n), "pattern", None), str)])

# ── C: ต่อสายเข้า Groom จริงทั้งสองสเต็ป ────────────────────────────────────
check("C rule is wired into the rewrite prompt",
      "{_GROOM_OBSERVABLE_RULE}\n{_GROOM_PRESERVE_RULE}\n{_GROOM_NARRATE_RULE}\n"
      "{_GROOM_POV_RULE}\n{_GROOM_ANATOMY_RULE}\n" in _SRC)
check("C permanent body facts are carried by the story itself",
      "THE BODY IS NOT YOURS TO CHANGE" in L._GROOM_ANATOMY_RULE
      and "DRESS PEOPLE IN THE PROSE" in L._GROOM_NARRATE_RULE)

# ── D: ไม่กลายเป็น Fill และไม่ over-correct ────────────────────────────────
check("D groom does not do renderer choreography",
      "her right biological hand" in _low and "never write" in _low)
check("D no left/right labelling through ordinary scenes",
      "never label limbs left and right through an ordinary scene" in _low)
check("D groom rule does not duplicate the downstream rule verbatim",
      _RULE != L._ANATOMY_INTERACTION_RULE
      and L._ANATOMY_INTERACTION_RULE not in _SRC.split("_GROOM_ANATOMY_RULE = (")[1].split("\n)\n")[0])
# หมายเหตุ: เดิมเทียบเป็นสัดส่วนกับ _ANATOMY_INTERACTION_RULE (< 1.6x) แต่หลังรอบบีบกฎ
# renderer-side ให้สั้นลงมาก (~600 ตัวอักษร) อัตราส่วนนั้นไม่มีความหมายอีกต่อไป — groom เขียน
# เพื่อ "รักษาความต่อเนื่องของร่างกายระหว่างเขียน prose" ซึ่งเป็นงานคนละแบบกับ renderer เจาะจงเจาะจง
# จึงยาวกว่าได้ตามธรรมชาติ ใช้เพดานตายตัวแทน (กันไม่ให้ groom โป่งกลับไปเป็น tutorial เหมือนเดิม)
check("D groom rule stays under an absolute cap (not a renderer-detail tutorial)",
      len(_RULE) < 3500, len(_RULE))
check("D groom is not told to output camera or visual staging",
      "camera" not in _low and "framing" not in _low)

# ── E: ไม่มี regression — กฎ groom เดิมยังอยู่ครบ ───────────────────────────
for _label, _probe in {
    "observable genre rule": "MAKE THE GENRE HAPPEN, DO NOT ANNOUNCE IT",
    "emotion is caused":     "EMOTION IS CAUSED, NEVER DECORATIVE",
    "unseen stays unseen":   "DO NOT RESOLVE WHAT THE SOURCE LEFT UNSEEN",
}.items():
    check(f"E {_label} still present", _probe in _SRC, _probe)

check("E groom is one call: story first, then its own final check",
      "You rewrite source material into a story a person reads, then you check your own work." in _SRC
      and "You extract a story card from raw source material" not in _SRC
      and inspect.getsource(L.run_story_grooming).count("await _call_llm_generic(") == 1)
check("E final check rides in the same response, after the story",
      "<<<GROOM_FINAL_CHECK>>>" in _SRC
      and "Write the whole story first, at full length" in L._GROOM_FINAL_CHECK_CONTRACT
      and "REPORT ONLY" in L._GROOM_FINAL_CHECK_CONTRACT)

# ── F: ไม่แตะ schema ───────────────────────────────────────────────────────
check("F no limb_map field added", "limb_map" not in _SRC)
check("F the story card and its python coverage heuristic are gone",
      not hasattr(L, "_parse_groom_card")
      and not hasattr(L, "groom_story_fact_coverage")
      and not hasattr(L, "_GROOM_VISUAL_FIELDS"))
check("F python validates the check's shape, never its meaning",
      "Python ไม่ตัดสินความหมายแทนโมเดล" in _SRC
      and L._parse_groom_final_check("not json at all") is None)
check("F rendering style is never narrated into the story",
      "THE STORY IS THE WORLD, NOT THE PHOTOGRAPH OF IT" in L._GROOM_NARRATE_RULE
      and "RENDERING STYLE IS NOT A STORY FACT" in L._GROOM_FINAL_CHECK_CONTRACT)

print()
print(f"{'ALL PASS' if not _fails else str(len(_fails)) + ' FAILED: ' + ', '.join(_fails)}")
