# BRIEF — Music Studio UI: Compact Navigation v1

## GOAL

ปรับ UI ของ WorkflowPad Music Studio เดิมให้ Minimal สวยงาม เป็นระเบียบ ใช้งานง่าย โดยเน้น **Right Panel แบบ Mobile Push Navigation**

ใช้โค้ดและฟังก์ชันที่มีอยู่จริงเป็นฐาน ห้ามสร้างโปรแกรมใหม่หรือเพิ่มฟีเจอร์จากภาพ Mockup

## 1. Preserve Existing Layout

คงโครงสร้างหลักทั้งหมด:

- **Top Tabs:** My Music และ Song/Editor Tabs เดิม
- **Left Panel:** Songwriting Assistant — Chat / Analysis
- **Center:** Lyrics Editor, Lyrics Tools Toolbar, Song Sheets
- **Right Panel:** เปลี่ยนเป็นเมนูแบบ Push Navigation
- **Output:** Generated Takes / History ของเพลงปัจจุบัน
- **Bottom:** Global Music Player

รักษาสัดส่วนหน้าจอเดิมเป็นหลัก ไม่ขยาย Right Panel จนบีบ Lyrics Editor

## 2. Right Panel — Mobile Push Navigation

### Main Menu

แสดงรายการเครื่องมือเป็นแนวตั้ง ใช้ไอคอนขนาดเล็กประมาณ 16–20px พร้อมชื่อและคำอธิบายสั้น ๆ

ตัวอย่างรายการจากระบบที่มีอยู่:

- Syllables
- Rhymes
- Rhythm
- Thesaurus
- AI Music Director
- Lyrics Tools / Section Options
- Style / Generate Song

จัดกลุ่มรายการตามหน้าที่เพื่อลดความรก

**สำคัญ:** ตรวจโค้ดจริงก่อนสร้างเมนู หาก Structure, Rhyme Suggest, Title Ideas หรือ Mood & Tone ในภาพตัวอย่างไม่มีระบบรองรับอยู่แล้ว ห้ามเพิ่มฟีเจอร์เหล่านั้น

### เมื่อกดรายการ

ตัวอย่างกด `Rhymes`:

1. Main Menu เลื่อนไปทางซ้าย
2. หน้า Rhymes เลื่อนเข้ามาทางขวา แทนที่ Main Menu
3. แสดง Header พร้อมปุ่ม `← Back` และชื่อ Rhymes
4. แสดงเครื่องมือและผลวิเคราะห์ Rhymes เดิมทั้งหมด
5. กด Back เพื่อกลับ Main Menu

**เปลี่ยนเฉพาะเนื้อหาภายใน Right Panel**

ห้ามเปลี่ยนหน้าทั้งแอป และห้ามทำให้ Left Chat, Lyrics Editor, Song Sheets, Output หรือ Player หายไป

### Navigation Rules

- ใช้ Navigation Stack ของ Right Panel แยกจาก Navigation หลัก
- เมื่อ Back ให้คืน Scroll Position ของเมนูเดิม
- กดเมนูซ้ำหรือกดเร็วต้องไม่เปิดหน้าซ้อน
- เปลี่ยน Song / Sheet แล้วต้องไม่แสดงผลวิเคราะห์ของเพลงผิดตัว
- หากมี LLM Request ทำงานอยู่ การเลื่อนเมนูหรือกด Back ต้องไม่ทำให้ State สูญหาย
- ไม่สร้าง Analysis Instance หรือระบบ Cache ซ้ำ
- ใช้ Animation สั้นและลื่นไหล หาก Flet รองรับโดยไม่กระทบประสิทธิภาพ
- รักษาพฤติกรรมเดิมของแต่ละเครื่องมือ

## 3. Compact Visual Refresh

ปรับหน้าตาให้อิง Style ของโปรแกรมเดิม:

- Dark Navy Theme เดิม
- Blue Accent เดิม
- ไอคอนเล็ก ขนาดสม่ำเสมอ
- ลด Padding / Spacing ที่เกินจำเป็น
- ใช้เส้นแบ่งบาง ๆ และ Border เรียบง่าย
- ลดพื้นที่ของการ์ดเมนูที่สูงเกินไป
- เน้นปุ่มสำคัญ เช่น Generate Song
- ไม่ใช้ Ribbon ใหญ่แบบ MS Word
- ไม่เพิ่ม Bold / Italic / Underline หรือเครื่องมือจัดเอกสาร

เป้าหมายคือ **Compact Productivity UI** ไม่ใช่ Redesign ใหม่ทั้งหมด

## 4. Generate Song

ใน Main Menu ให้มีรายการ `Generate Song` ที่มองเห็นชัด

เมื่อกด ให้เปิดหน้า Generate เดิมภายใน Right Panel เพื่อแสดง Settings, Basic/Advanced Style, Music Caption และปุ่ม Generate ตามที่โค้ดรองรับ

**การกดรายการเมนูต้องไม่เริ่ม Generate ทันที**

ไม่เปลี่ยนการส่ง Workflow ไป ComfyUI และไม่เปลี่ยนการจัดเก็บ Takes

Music Caption ยังคงเป็นภาษาอังกฤษเสมอ

## 5. Preserve Existing Systems

ห้ามเปลี่ยน Logic ของ:

- Lyrics Editor / Cursor / Selection / Scroll
- Chat / AI Actions / Prompt
- Syllables / Rhymes / Rhythm / Thesaurus
- Thai Rhyme Verification
- Song Sheets / Version Comparison
- Music Director / Caption Generation
- ComfyUI / ACE-Step
- Songs / Takes / History / Player
- Persistence / Cache / Autosave

UI ที่เปลี่ยนต้องเรียกใช้ Function เดิม ไม่สร้าง Logic คู่ขนาน

## 6. Tests

ทำ Static / Mock Tests โดยไม่เรียก LLM หรือ ACE-Step

ตรวจ:

1. Main Menu → Detail → Back
2. Scroll Restoration และการกดเมนูเร็ว ๆ
3. เปลี่ยน Song / Sheet ขณะเปิดหน้า Detail
4. ผล Analysis ไม่สลับเพลง
5. LLM Request ไม่สูญหายเมื่อเปลี่ยนหน้า
6. Generate Settings / Output ยังทำงานเหมือนเดิม
7. Responsive Layout เมื่อหน้าต่างเล็กลง
8. ไม่มี Regression ใน Editor Scroll, Cursor และ Selection

ถ้าทดสอบ Flet UI จริงได้ ให้ตรวจการเลื่อนหน้าและการจัด Layout ด้วย หากทำไม่ได้ระบุ Not Tested

## 7. Deliverables

- ไฟล์โค้ดที่แก้
- Unified Diff
- รายการ UI ที่ย้าย/รวม/ซ่อน
- ผล Tests
- Known Limitations

**ไม่ Commit และไม่เปลี่ยนข้อมูลเพลงเดิม**

## ACCEPTANCE

ผู้ใช้เปิด Music Studio แล้วเห็น Layout เดิมที่สะอาดและกระชับขึ้น

เมื่อกดเครื่องมือด้านขวา จะเข้าสู่หน้ารายละเอียดด้วยการเลื่อนแบบมือถือ และสามารถกด Back กลับได้ทันที

ทุกฟังก์ชันเดิมยังทำงานครบ โดยไม่มีฟีเจอร์ใหม่เพิ่มเข้ามา

**ขอบเขตงานรอบนี้: UI Navigation + Visual Cleanup เท่านั้น**
