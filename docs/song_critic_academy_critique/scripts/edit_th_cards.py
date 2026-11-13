# -*- coding: utf-8 -*-
import json, sys
tp = sys.argv[1] if len(sys.argv) > 1 else "D:/AI/MyApps/WorkflowPad/music_studio/locales/th.json"
raw = open(tp, "rb").read().decode("utf-8")
crlf = "\r\n" in raw
t = raw.replace("\r\n", "\n")
d = json.loads(t)
new = {
    "Done well": "ทำได้ดี", "Could be improved": "มีจุดควรปรับปรุง", "Major issue": "มีปัญหาสำคัญ", "Not relevant to this song": "ไม่เกี่ยวข้องกับเพลงนี้",
    "Not analyzed / not enough information": "ยังไม่ได้วิเคราะห์ / ข้อมูลไม่เพียงพอ",
    "Read the lesson": "อ่านบทเรียน",
    "Open the lesson on this aspect (reference knowledge, not part of the critique)": "เปิดบทเรียนของหัวข้อนี้ (ความรู้อ้างอิง ไม่ใช่ส่วนของบทวิจารณ์)",
    "Open the Songwriter chat with this critique and its evidence; nothing changes in the lyrics": "เปิดแชตผู้ช่วยแต่งเพลงพร้อมบทวิจารณ์นี้และหลักฐาน ไม่มีอะไรเปลี่ยนในเนื้อเพลง",
    "AI critique \u00b7 8 aspects": "บทวิจารณ์จาก AI \u00b7 8 หัวข้อ",
    "No critique yet: press Analyze. The lesson can be read now.": "ยังไม่มีบทวิจารณ์: กด Analyze ส่วนบทเรียนอ่านได้ตอนนี้",
    "An older report has no critique for this aspect: press Analyze again.": "รายงานรุ่นเก่าไม่มีบทวิจารณ์ของหัวข้อนี้: กด Analyze ใหม่",
    "The critique of your lyrics appears here after Analyze. The lessons are reference knowledge and can be read at any time; no AI is asked to show them.":
        "บทวิจารณ์เนื้อเพลงของคุณจะแสดงที่นี่หลังกด Analyze ส่วนบทเรียนเป็นความรู้อ้างอิง อ่านได้ทุกเมื่อ และไม่ได้เรียก AI มาแสดง",
    "View proposed change": "ดูข้อเสนอแก้ไข",
    "Close": "ปิด",
}
add = "".join(f"    {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)},\n" for k, v in new.items() if k not in d["strings"])
head = '  "strings": {\n'
assert t.count(head) == 1
t = t.replace(head, head + add)
json.loads(t)
open(tp, "wb").write((t.replace("\n", "\r\n") if crlf else t).encode("utf-8"))
print("added", sum(1 for k in new if k not in d["strings"]), "of", len(new))
