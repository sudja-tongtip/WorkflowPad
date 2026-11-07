# AI Coder Brief — Unified Numbered Clear Tags Migration

**Project:** WorkflowPad Music Studio  
**Scope:** All LLM Output Contracts  
**Priority:** P0 — Format Reliability / Parser Stability / UI Safety  
**Execution:** Audit → Implement → Test → Report

## 1. เป้าหมาย

ปรับระบบ WorkflowPad Music Studio ให้ **LLM ทุกตัวใช้ Numbered Clear Tags เป็นมาตรฐานเดียวกันสำหรับข้อความตอบกลับที่โปรแกรมต้องอ่าน**

ตัวอย่าง:

```text
[REPLY.1]
เนื้อเพลงท่อนนี้มีปัญหาเรื่องคำลงท้าย
ที่ฟังเหมือนเติมมาเพื่อให้สัมผัสกัน

[FINDING.1]
L028|wording|important
คำว่า "พวกนี้ที" อาจฟังไม่เป็นธรรมชาติ

[DONE]
```

ต้องแก้ปัญหา:

- LLM ส่ง JSON หรือ XML ผิด Format
- Parser อ่านไม่ได้จนข้อมูลทั้งชุดเสีย
- ข้อมูลซ้ำหรือขัดกันเอง
- UI แสดง Technical Errors ยาว ๆ
- Output ยาวเกินจำเป็นและเปลือง Token
- Format ของแต่ละ Specialist ไม่เหมือนกัน

**เป้าหมายหลักคือ Format Reliability ไม่ใช่ปรับความสามารถในการคิดของ LLM**

ห้ามใช้การย้าย Format ครั้งนี้เป็นเหตุผลไปเปลี่ยน Logic วิเคราะห์สัมผัส การแต่งเพลง หรือ Music Generation

---

## 2. Audit ทุก LLM Call Site ก่อนแก้

ตรวจจากโค้ด Production จริงทั้งหมด ไม่ใช่ตรวจเฉพาะชื่อ Specialist

ต้องครอบคลุมอย่างน้อย:

| ระบบ | สิ่งที่ต้องตรวจ |
|---|---|
| Songwriter AI | Chat, วิเคราะห์เพลง, Edit Proposal, Counting |
| Music Producer AI | Chat, วิเคราะห์เพลง, Arrangement, Cues |
| Rhyme Analyzer | End Groups, Internal Rhyme, Repetition |
| AI Recommend | Style, BPM, Music Settings |
| Caption Generation | Music Caption สำหรับ ACE-Step |
| AI Actions | Rewrite, Continue, Improve, Arrange |
| Preview | การเสนอ Section Cues และคำแนะนำ |
| Internal LLM Tasks | งานที่เรียกโมเดลเบื้องหลังทั้งหมด |
| Local / Grok | ความแตกต่างของ Response Handling |

ค้นหาจริงจากจุดเรียก LLM, Prompt Builder, Response Parser, Adapter และ Consumers

สำหรับแต่ละงาน ให้บันทึก:

- Call Site และฟังก์ชันที่เกี่ยวข้อง
- Backend ที่รองรับ
- Output Format ปัจจุบัน
- Parser ที่ใช้งาน
- UI หรือระบบที่รับผล
- มี Side Effect หรือ Apply Action หรือไม่
- ความเสี่ยงของการ Migration

**Deliverable:** `LLM_FORMAT_AUDIT.md`

ห้ามถือว่าทุก JSON ในโปรเจกต์เป็น LLM Output

JSON สำหรับ ComfyUI API, Grok API, Project Storage, Cache, Settings และไฟล์ระบบภายในต้องคงเดิม เว้นแต่เป็นส่วนของ LLM Response Contract โดยตรง

---

## 3. ออกแบบ Unified Numbered Clear Tags

สร้างมาตรฐานกลางหนึ่งชุด

### ตัวอย่าง Tag Registry

| Tag | หน้าที่ |
|---|---|
| `[REPLY.n]` | คำตอบ Chat |
| `[FINDING.n]` | ข้อสังเกต |
| `[REVIEW.n]` | ผลวิเคราะห์ |
| `[EDIT.n]` | ข้อเสนอแก้เนื้อเพลง |
| `[SECTION.n]` | ข้อมูล Section |
| `[END.n]` | กลุ่มสัมผัสท้าย |
| `[LINK.n]` | สัมผัสประเภทอื่น |
| `[REPEAT.n]` | คำซ้ำ |
| `[STYLE.n]` | Style Settings |
| `[SETTING.n]` | ค่าตั้งต่าง ๆ |
| `[CAPTION.n]` | Music Caption |
| `[DONE]` | จบคำตอบ |

รายการนี้เป็นจุดเริ่มต้น ให้ Audit ก่อนสรุป Tag Registry ฉบับจริง

### กฎกลาง

1. รูปแบบ Tag คือ `[TYPE.n]`
2. ใช้ชื่อ Tag ที่ Registry รองรับเท่านั้น
3. หมายเลขต้องไม่ซ้ำภายในประเภทเดียวกันของคำตอบหนึ่งชุด
4. Payload รองรับหลายบรรทัด
5. ต้องกำหนดกฎ Escape สำหรับข้อความที่มีหน้าตาเหมือน Tag
6. ต้องรักษาเนื้อเพลงต้นฉบับตรงทุกตัวอักษร
7. ไม่มี Markdown Fence หรือคำอธิบายนอก Contract สำหรับงาน Structured Output
8. ไม่บังคับให้ทุกงานส่งทุก Tag
9. ไม่สร้างข้อมูลหรือ Tag เปล่าเพื่อให้ครบตัวอย่าง
10. ห้ามรายงานข้อมูลเดียวกันหลายชุดโดยไม่จำเป็น

**สำคัญ:** Parser ต้องแยก Tag ที่เป็นคำสั่งออกจาก `[Verse]`, `[Chorus]`, `[Hold]` และข้อความในเนื้อเพลงได้ โดยไม่มีการแก้เนื้อเพลงของผู้ใช้

---

## 4. Shared Clear Tag Parser

สร้าง Parser กลางที่ทุก Specialist เรียกใช้ร่วมกัน

แนะนำแยกเป็น:

- Core Tag Parser — อ่าน Syntax
- Tag Registry — ระบุชนิด Tag
- Task Schema — ตรวจฟิลด์ของแต่ละงาน
- Task Adapter — แปลงเข้าสู่ Data Model เดิม
- Diagnostics — บันทึกข้อผิดพลาด

ไม่สร้าง Regex Parser คนละชุดในแต่ละ Specialist

### Parser Requirements

ต้องรองรับ:

- Multiline Payload
- Thai / English / Unicode
- Multiple Tags
- Duplicate Tag Numbers
- Unknown Tags
- Missing Fields
- Missing `[DONE]`
- Incomplete Response
- Extra Text
- Literal Tag ใน Lyrics
- Partial Recovery

### Error Handling

ถ้ารายการใดผิด ให้พยายามเก็บรายการอื่นที่ถูกต้อง

แต่ต้องแยก **Syntax Valid**, **Schema Valid** และ **Safe to Use** ให้ชัดเจน

ห้าม Parser:

- เดาค่าที่ LLM ไม่ได้ส่ง
- ตัดสินเสียงสัมผัสใหม่
- สร้างข้อเสนอแก้เพลงแทนโมเดล
- แก้ค่าดนตรีตามความคาดเดา
- เปลี่ยนเนื้อเพลง
- นำข้อมูลที่ตรวจไม่ผ่านไป Apply

Chat Text สามารถกู้คืนได้ตามความเหมาะสม แต่ **Edit / Music Settings / Actions ที่มีผลต่อข้อมูลต้องผ่าน Schema และ Safety Validation ก่อนเสมอ**

---

## 5. Migration — Rhyme Analyzer

เปลี่ยน Output Contract จาก JSON เป็น Numbered Clear Tags

ใช้:

```text
[SECTION.1]
[END.1]
L12|ชัวร์
L13|กลัว

[LINK.1]
exact|internal
L14|คำหนึ่ง
L14|คำสอง

[ENDSECTION.1]
[DONE]
```

ตัวอย่าง LINK เป็นเพียงตัวอย่าง Syntax ไม่ใช่ผลตรวจเสียงจริง

### รักษา Logic เดิม

- การวิเคราะห์เสียงสามส่วน: สระ / สั้น-ยาว / ตัวสะกด
- Exact / Near / Uncertain
- Internal Rhyme
- Repetition
- กฎวงเล็บ `( ... )`
- การจำกัดคู่ข้าม Section

End Group เป็นแหล่งข้อมูลหลักของสัมผัสท้าย

ไม่ส่ง Exact End-to-End Pair ซ้ำใน LINK

Python ตั้งชื่อ A/B/C และกำหนดสีตามกลุ่มที่ LLM ส่งมาเท่านั้น

**ห้ามใช้ Python ตรวจหรือแก้เสียงสัมผัสแทน LLM**

บรรทัดที่ไม่ได้รับกลุ่มให้แสดง `—` สีเทา โดยไม่ตีความว่าไม่มีสัมผัสแน่นอน

---

## 6. Migration — Songwriter AI

เปลี่ยนทั้ง Chat Contract และ Structured Report Contract

ต้องรองรับ:

- คำตอบธรรมชาติ
- การวิเคราะห์โดยไม่แก้เนื้อเพลง
- ข้อเสนอแก้เฉพาะบรรทัด
- ข้อเสนอหลายทางเลือก
- Short Confirmation เช่น “เอาแบบนั้น”
- Cancel / Reject
- No-op
- Analyze Song Report

ตัวอย่าง:

```text
[REPLY.1]
Hook นี้มีความหมายชัด แต่บรรทัดสุดท้าย
ยังฟังเหมือนเติมคำเพื่อให้ลงสัมผัส

[DONE]
```

สำหรับ Edit Proposal ให้กำหนด Payload Contract ที่รองรับ Target, Original Text และ Proposed Text อย่างครบถ้วน รวมถึงข้อความหลายบรรทัด

**ห้ามแปลง Edit เป็นการเปลี่ยนเนื้อเพลงทันที**

ต้องรักษา:

- Preview
- Apply
- Reject
- Undo / Redo
- การแก้เฉพาะ Target
- การไม่แก้ Lyrics ระหว่าง Analyze / Count / Critique

ห้ามลดความสามารถ Songwriter เพื่อให้ Format สั้นลง

---

## 7. Migration — Music Producer AI และ Internal Actions

ปรับ Structured Output ของ Music Producer และ AI Actions ให้เป็น Numbered Clear Tags

ต้องรองรับข้อมูลเดิมทั้งหมด เช่น:

- BPM
- Style
- Key
- Duration
- Vocal Cues
- Section Arrangement
- Music Caption
- AI Recommended Settings
- Music Structure Suggestions

แต่ละงานมี Task Schema เฉพาะ

Parser ต้องตรวจชนิดข้อมูลและช่วงค่าตามกฎเดิมก่อนส่งเข้า UI

รักษาพฤติกรรมการเสนอให้ผู้ใช้ Preview / Apply ก่อนเปลี่ยนค่าที่ต้องได้รับการยืนยัน

ห้ามแก้ ACE-Step Workflow, Model Parameters, Song Duration Logic หรือการทำงานของ Backend เพียงเพราะเปลี่ยน Output Format

---

## 8. Prompt Migration

ตรวจ Prompt จริงที่โปรแกรมโหลดก่อนแก้

### ข้อกำหนด

- ใช้ Prompt .md ที่ผู้ใช้แก้ไขเองได้ต่อไป
- ไม่เขียนทับ Custom Prompt ใน AppData
- ไม่เพิ่ม Few-shot ยาวเกินจำเป็น
- ให้ Format Contract อยู่ใน Task Template หรือส่วนกลางที่เหมาะสม
- ไม่มีคำสั่ง JSON/XML เก่าขัดกับ Clear Tags
- ไม่ทำให้ Chat กลายเป็นรายงานหุ่นยนต์
- ไม่เปลี่ยนบุคลิกของ Songwriter หรือ Producer
- ไม่แก้ Session / History Management

ต้องแยก **System Role**, **Task Instructions** และ **Output Contract** ให้ชัดเจน

โมเดลจะใช้ Local Gemma หรือ Grok ก็ต้องตอบด้วย Contract เดียวกันในงานประเภทเดียวกัน

---

## 9. UI Error Handling

แก้การแสดงผล Technical Errors ที่รบกวนหน้าจอ

จากเดิมที่แสดงข้อความยาว เช่น:

`it lists L12 ... but they are not in one end group`

ให้แสดงสถานะสั้น ๆ เช่น:

**ผลวิเคราะห์บางส่วน — มีข้อมูลบางรายการไม่สมบูรณ์**

รายละเอียดทั้งหมดต้องอยู่ใน Diagnostic Log

กฎ:

- ไม่แสดง Raw Parser Error เต็มหน้ารายงาน
- ไม่ซ่อนว่าผลบางส่วนไม่สมบูรณ์
- แสดงเฉพาะข้อมูลที่ผ่าน Validation
- ไม่เติมข้อมูลที่โมเดลลืม
- ข้อมูลที่ผิดต้องไม่ถูกส่งให้ Songwriter เหมือนเป็นหลักฐานที่ตรวจสอบแล้ว

รักษา Layout, สี, Highlight, Rhyme Map และการ์ดรายงานเดิม

---

## 10. Backward Compatibility

สำคัญมาก: ไม่ใช่เปลี่ยน Parser แล้วทำให้ข้อมูลเก่าเปิดไม่ได้

ต้องรองรับ:

- JSON Rhyme Reports เดิม
- XML Edit Proposals ที่ยังอยู่ในข้อมูลหรือ History
- Cached Reports
- ข้อมูล Song / Project เดิม

ให้มี Versioned Adapter สำหรับข้อมูลเดิม และไม่เขียนทับข้อมูลเก่าโดยไม่จำเป็น

สำหรับข้อมูลที่บันทึกใหม่ ใช้ Clear Tag Contract เวอร์ชันใหม่

---

## 11. Testing — ต้องทำจริงในรอบนี้

### A. Mock / Unit Tests

ทดสอบ Shared Parser ด้วย:

- Valid Tags
- Missing Tag
- Duplicate Number
- Unexpected Tag
- Broken Payload
- Missing DONE
- Partial Recovery
- Unicode / Thai Text
- Multiple Sections
- Literal Tags in Lyrics
- Multiline Edit
- Wrong Target
- Duplicate Actions
- Safe No-op

### B. Specialist Regression

ทดสอบทุกงานที่พบจาก Audit

โดยเฉพาะ:

- Songwriter Chat / Edit / Analyze
- Music Producer Chat / Settings / Analyze
- Rhyme Analyzer
- AI Recommend
- Caption
- Arrange
- Preview

ต้องตรวจว่า Output ผ่าน Shared Parser แล้วทำงานต่อกับ Adapter / UI เดิมได้

### C. Safety Regression

ทดสอบให้แน่ใจว่า:

- Analyze Only ไม่สร้าง Edit
- Count Only ไม่แก้ Lyrics
- Invalid Edit ไม่สามารถ Apply ได้
- Reject ไม่เปลี่ยนข้อมูล
- Undo / Redo ยังทำงาน
- Missing Fields ไม่ถูกเติมค่าเองอย่างเงียบ ๆ
- ไม่มี LLM Call ระหว่างพิมพ์ Lyrics
- ไม่ส่ง AI Chat History ข้าม App Restart

### D. Real LLM Smoke Tests

หลัง Mock ผ่าน ให้ทดสอบกับ Backend จริงที่พร้อมใช้งาน

ใช้จำนวนรอบเท่าที่จำเป็น ไม่ต้องทำ Deep Rhyme Accuracy Audit ใหม่

วัด:

- Format Pass
- Parse Success
- Partial Recovery
- Output Size / Tokens
- Latency
- UI Compatibility

หาก Backend ใดไม่พร้อม ให้รายงานว่าไม่ได้ทดสอบจริง ห้ามอ้างว่าผ่าน

**ห้าม Generate Audio เพื่อทดสอบ Format**

---

## 12. Acceptance Criteria

ถือว่างานผ่านเมื่อ:

1. Audit ครบทุก LLM Call Site ที่พบ
2. ทุก Application-facing LLM Output Contract ที่อยู่ใน Scope เปลี่ยนเป็น Numbered Clear Tags
3. มี Shared Parser และ Task Adapters
4. ไม่มี JSON/XML Output Instruction เก่าที่ขัดกันในเส้นทางที่ย้ายแล้ว
5. Mock Tests ผ่าน
6. Structured Actions ปลอดภัย
7. Chat ยังตอบเป็นธรรมชาติ
8. UI ไม่แสดง Raw Technical Errors
9. Reports และ Projects เก่ายังใช้งานได้
10. ไม่มี Regression ใน Preview / Apply / Undo
11. มีผลทดสอบจริงและข้อจำกัดชัดเจน

หากพบ Call Site ที่ย้ายไม่ได้อย่างปลอดภัย ให้เก็บ Compatibility Path ชั่วคราว ระบุไว้ในรายงาน และอย่าอ้างว่า Migration ครบ 100%

---

## 13. Deliverables

ส่งมอบ:

**`LLM_FORMAT_AUDIT.md`**
- รายชื่อ Call Sites
- Format เดิม
- Parser เดิม
- ผลการ Migration
- จุดที่ยังมีข้อจำกัด

**`CLEAR_TAG_CONTRACT.md`**
- Tag Grammar
- Registry
- Task Schemas
- Escape Rules
- ตัวอย่างของทุก Specialist
- Versioning

**`CLEAR_TAG_MIGRATION_REPORT.md`**
- รายการไฟล์ที่แก้
- ผล Unit / Mock / Integration Tests
- ผล Real LLM Tests
- Format Errors ที่พบ
- Before / After Output Size
- Regression / Known Limitations

เก็บ Raw LLM Responses ที่ใช้ทดสอบไว้ในโฟลเดอร์ Audit โดยไม่ปะปนกับข้อมูลเพลงจริง

---

## 14. Execution Rules

**ให้ดำเนินการ Audit → Implement → Test ต่อเนื่องได้เลย ไม่ต้องหยุดขออนุมัติระหว่างขั้นตอน**

แต่ต้อง:

- ตรวจโค้ดจริงก่อนแก้
- แก้เฉพาะส่วนที่เกี่ยวกับ Output Contract / Parser / Adapter / UI Error Handling
- ไม่เปลี่ยน Model หรือ Backend
- ไม่เปลี่ยนการจัดการ Sessions
- ไม่ทับ Custom Prompts ของผู้ใช้
- ไม่เปลี่ยน Songwriting Logic
- ไม่เพิ่ม Python Phonetic Verification
- ไม่ลบข้อมูลเพลง
- ไม่ Commit จนกว่าจะส่งผลตรวจสอบ

หากพบความเสี่ยงกับข้อมูลผู้ใช้ ให้ใช้ Compatibility Layer แทนการบังคับ Migration ที่อาจทำข้อมูลเสีย

## Final Principle

**One Tag Grammar — Shared Parser — Task-Specific Schemas — Safe UI**

ทุก LLM ใช้มาตรฐานเดียวกัน แต่ยังทำหน้าที่เดิม

เป้าหมายไม่ใช่เปลี่ยนวิธีคิดของ AI แต่ทำให้คำตอบของ AI ถูกอ่าน แสดงผล และนำไปใช้งานได้อย่างเสถียร โดยไม่สร้างข้อผิดพลาดต่อเนื่องไปถึงผู้ใช้
