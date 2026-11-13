# -*- coding: utf-8 -*-
import json, sys
tp = sys.argv[1] if len(sys.argv) > 1 else "D:/AI/MyApps/WorkflowPad/music_studio/locales/th.json"
raw = open(tp, "rb").read().decode("utf-8")
crlf = "\r\n" in raw
t = raw.replace("\r\n", "\n")
d = json.loads(t)
OLD_KEYS = ["Source checked", "Lessons are reference knowledge, not the AI's opinion about your song. [R1] marks a principle that a cited reference supports; text with no tag is this program's own synthesis; the examples were written for teaching."]
new = {
    "Lessons are reference knowledge, not the AI's opinion about your song. The techniques are options for the writer, not rules: a song that does not use them is not wrong. [R1] marks a statement that a cited reference page makes; text with no tag is this program's own synthesis, not an endorsed theory; the examples were written for teaching.":
        "บทเรียนเป็นความรู้อ้างอิง ไม่ใช่ความเห็นของ AI เกี่ยวกับเพลงของคุณ เทคนิคเป็นทางเลือกของผู้แต่ง ไม่ใช่กฎ เพลงที่ไม่ใช้เทคนิคเหล่านี้ไม่ได้ผิด [R1] หมายถึงข้อความที่หน้าอ้างอิงนั้นระบุไว้ ส่วนข้อความที่ไม่มีแท็กคือการสังเคราะห์ของโปรแกรมนี้เอง ไม่ใช่ทฤษฎีที่ใครรับรอง และตัวอย่างแต่งขึ้นเพื่อการสอน",
    "Reference page checked (confirms only what is stated)": "ตรวจหน้าอ้างอิงแล้ว (ยืนยันเฉพาะที่ระบุไว้)",
    "Open source page": "เปิดหน้าแหล่งอ้างอิง",
    "Nothing was sent to the model: the song or sheet changed before the message went out, so the lesson or report was not attached. Press Discuss again.":
        "ยังไม่ได้ส่งอะไรให้โมเดล: เพลงหรือชีตเปลี่ยนก่อนส่งข้อความ จึงไม่ได้แนบบทเรียนหรือรายงาน กด Discuss อีกครั้ง",
}
# drop the two texts that were replaced (they would be dead keys)
for k in OLD_KEYS:
    line_start = t.find("    " + json.dumps(k, ensure_ascii=False) + ":")
    if line_start >= 0:
        line_end = t.index("\n", line_start) + 1
        t = t[:line_start] + t[line_end:]
d = json.loads(t)
add = "".join(f"    {json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)},\n" for k, v in new.items() if k not in d["strings"])
head = '  "strings": {\n'
assert t.count(head) == 1
t = t.replace(head, head + add)
json.loads(t)
open(tp, "wb").write((t.replace("\n", "\r\n") if crlf else t).encode("utf-8"))
print("added", sum(1 for k in new if k not in d["strings"]), "of", len(new), "removed", len(OLD_KEYS))
