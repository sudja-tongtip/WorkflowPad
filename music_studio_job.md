# BRIEF — Music Studio: Full Intelligence & Quality Upgrade

## GOAL

ยกระดับ WorkflowPad Music Studio ให้ AI วิเคราะห์เนื้อเพลงไทยได้แม่นยำขึ้น สร้างคำแนะนำทางดนตรีที่มีคุณภาพ และช่วยลดปัญหาเสียงร้องภาษาไทยไม่เป็นธรรมชาติ

**คุณมีอิสระในการออกแบบและแก้ Code, Prompt, Validation และ Workflow ตามความจำเป็น ไม่ต้องรออนุมัติทีละ Patch**

แต่ต้องตรวจโค้ดล่าสุดก่อน ไม่สร้างระบบซ้ำ ไม่รื้อส่วนที่ทำงานดีแล้ว และต้องรักษาข้อมูลเดิม

## 1. Rhymes — Priority สูงสุด

ทำให้ระบบตรวจสัมผัสภาษาไทยได้จริง ไม่ใช่เพียงตรวจว่าคำอยู่ในบรรทัด

- ตรวจและปรับ System Prompt ของ `lyric_rhyme.py` ให้เข้าใจเสียงสระ ความยาวสระ และเสียงตัวสะกดจริง
- เพิ่มตัวอย่างคู่คำที่สัมผัสและไม่สัมผัส เช่น ดาว–หนาว, ใจ–ไป, กาน–กิน, บ้าน–ฝัน
- รองรับ `end`, `internal`, `near`, `cross` และสัมผัสท้ายบรรทัด → กลางบรรทัด
- เพิ่ม Thai Phonetic Verification ด้วยวิธีที่เชื่อถือได้ ไม่ใช้ Regex เปรียบเทียบรูปสะกดแล้วอ้างว่าเป็นการตรวจเสียง
- แยกผล **Verified / Unverified / Rejected** ออกจาก Confidence ที่ LLM รายงานเอง
- เมื่อ Python ยืนยันเสียงไม่ได้ ให้แสดง Unverified ไม่เดาว่าถูกหรือผิด
- ตรวจตำแหน่งคำจริง แม้ภาษาไทยไม่มีช่องว่างระหว่างคำ
- ป้องกันการจับคู่คำที่ไม่มีอยู่จริง หรือคำที่เป็นส่วนหนึ่งของคำอื่นผิดตำแหน่ง
- ตรวจ Schema, UI, Serialization และ Cache ให้รองรับการเปลี่ยนแปลง โดยไม่ทำข้อมูลเก่าเสีย

หากจำเป็นต้องใช้ G2P หรือแหล่งข้อมูลเสียงอ่าน ให้ประเมินความแม่นยำ ข้อจำกัด Dependency และวิธี Fallback ก่อนเลือกใช้ ห้ามเพิ่ม Dependency หนักโดยไม่มีเหตุผล

**Acceptance:** ระบบต้องไม่แสดงคู่สัมผัสผิดอย่างมั่นใจโดยไม่มีหลักฐาน และต้องแสดงคู่สัมผัสข้ามบรรทัดได้

## 2. Syllables + Rhythm + Thesaurus

### Syllables

- ตรวจความแม่นยำการหั่นพยางค์ไทย โดยเฉพาะอักษรนำ สระแฝง คำควบกล้ำ คำยืม และคำอ่านหลายพยางค์
- ปรับ Prompt ตามข้อผิดพลาดที่พบจริง
- แยกการนับพยางค์ตามเสียงอ่านจากจำนวนเสียงร้องจริงที่ยังไม่มี Audio ให้ตรวจ
- รักษา Manual Corrections และ Cache เดิม

### Rhythm

- ตรวจ One-Click Rhythm ให้ทำงานครบ: Syllables → Rhythm
- ป้องกัน Async Race, คำขอซ้ำ และผลล้าสมัย
- ใช้ Effective Syllable Counts รวมการแก้มือจริง
- ไม่อ้างว่ารู้ Melody หรือ Groove จาก Lyrics อย่างเดียว
- ปรับคำแนะนำเรื่องการหายใจและ Phrase Density เท่าที่ข้อมูลรองรับ

### Thesaurus

- ตรวจความหมายของคำทดแทนและความเป็นธรรมชาติของภาษาไทย
- รักษา Selection/Offset และขอบเขตการแก้ไข
- ไม่เปลี่ยนความหมายโดยไม่บอกผู้ใช้
- ตรวจระบบ Replace ที่มีอยู่จริง ไม่เพิ่มระบบซ้ำ

### Temperature

- ตรวจ `analysis_temperature` และ Cache Key
- แยกค่า Analysis ออกจาก Chat อย่างปลอดภัย
- ไม่บังคับให้ทั้ง 4 แท็บใช้ Temperature เดียวกันโดยไม่มีเหตุผล
- ทดลองค่าที่เหมาะสมจากผลทดสอบ ไม่ถือว่า 0.1–0.2 ถูกต้องที่สุดเสมอ

## 3. Basic / Effective Caption

ตรวจและปรับ `basic_caption.py` และส่วนที่เชื่อมกับ ACE-Step

**Goal:** ให้ Music Caption มีรายละเอียดทางดนตรี เหมาะกับ Lyrics และเคารพ Settings ที่ผู้ใช้เลือก

- ตรวจ Prompt ที่มีอยู่ก่อน ไม่เขียนซ้ำ
- ปรับ Musical Specificity เฉพาะส่วนที่ขาด เช่น Instrumentation, Arrangement, Groove, Production, Vocal Delivery
- วิเคราะห์เนื้อเพลงจากอารมณ์และโครงสร้าง ไม่จับคำว่า ฝน/คืน/ไฟ แล้วเติม Sound Effect แบบตรงตัว
- อย่าเปลี่ยน Genre/Vocal/Energy ของผู้ใช้โดยพลการ
- ไม่ตีความ High Energy ว่าต้องร้องเสียงสูงหรือ Belting เสมอ
- ตรวจ Effective Caption Viewer, Stale State และ Fallback ให้ถูกต้อง
- ตรวจว่า Caption ที่แสดงเป็น Current Prompt หรือ Caption ที่ใช้ Generate ล่าสุด

## 4. AI Recommend / Music Director

ตรวจ `ai_music_director.py` และ UI ที่เกี่ยวข้อง

- สาม Music Directions ต้องแตกต่างกันอย่างมีสาระด้าน Arrangement, Instrumentation, Vocal และ Production ไม่ใช่แค่เปลี่ยนชื่อ
- ลดคำแนะนำ Generic/Cliché
- BPM, Key และ Duration ต้องนำเสนอเป็นข้อเสนอ ไม่อ้างว่าคำนวณจาก Melody ที่ไม่มี
- ตรวจ Partial Apply, Field Selection และ Restore Safety
- ห้าม Restore ทับค่าที่ผู้ใช้แก้เองภายหลัง
- รักษา Stale Snapshot Protection และ Sheet Isolation
- ปรับ Prompt เฉพาะจุดที่ทำให้คำแนะนำไม่มีคุณภาพ

## 5. Thai Vocal Naturalness

นี่คือเป้าหมายด้านคุณภาพเสียงที่สำคัญ

ผู้ใช้พบว่า ACE-Step บางครั้งร้องภาษาไทยในช่วงเสียงสูงแล้วฟังฝืนและไม่เป็นธรรมชาติ

ให้ตรวจว่า Caption ของ Basic และ AI Recommend กำลังผลักให้เกิดพฤติกรรมนี้หรือไม่ เช่น `soaring vocals`, `powerful belting`, `explosive high notes`

ปรับแนวทาง Vocal Direction ให้คำนึงถึง:

- Natural Thai pronunciation
- Comfortable vocal range
- Natural phrasing and breath
- Controlled vocal peaks
- Emotional impact through arrangement rather than unnecessary high-register belting

อย่าบังคับให้ทุกเพลงร้องช่วงเสียงกลาง เพราะผู้ใช้ยังต้องเลือกแนวร้องพลังสูงได้ตามต้องการ

แยกให้ชัดว่าปัญหาใดแก้ได้ด้วย Lyrics, Caption หรือเป็นข้อจำกัดของ ACE-Step

## 6. Preserve Existing Work

ห้ามทำให้ระบบต่อไปนี้เสีย:

- Songwriting Craft v2 + Patch 1B/1C
- REPLY / PROPOSAL / END Parsing
- Lyrics Editor และ Caret/Selection
- Manual Corrections
- Existing Song/Sheet Data
- Generated Takes / History
- ComfyUI Workflow
- Existing Cache / Persistence

ตรวจ Patch ล่าสุดที่เคยทำไว้ก่อน ห้ามเขียนทับด้วยโค้ดเวอร์ชันเก่า

## 7. Tests — ทำให้ครบในรอบเดียว

AI Coder ดำเนินการเองได้ ไม่ต้องถามยืนยันทุกขั้น

**Static / Unit / Mock Tests**
- ทดสอบทุกโมดูลที่เปลี่ยน
- ทดสอบภาษาไทยที่รู้ผลถูกต้องแน่นอน
- ทดสอบคู่สัมผัสจริง/เทียม/ข้ามบรรทัด
- ทดสอบ Cache, Stale Results, Restore, Serialization และ Regression
- ทดสอบ One-Click Rhythm ตั้งแต่ต้นจนจบด้วย Mock ที่รองรับ Interface ล่าสุด

**Real LLM Tests**
- ใช้ Gemma4 กับ Test Case ที่เจาะจง
- เก็บ Raw Output เพื่อวิเคราะห์คุณภาพ
- หากต้องเปรียบเทียบ ให้เปลี่ยนเพียงตัวแปรที่ต้องการพิสูจน์
- หลีกเลี่ยงการรันจำนวนมากโดยไม่จำเป็น

**ACE-Step Audio**
- ไม่จำเป็นต้อง Generate เพลงจริงในรอบนี้
- รายงานสิ่งที่ต้องทดสอบเสียงบนเครื่องผู้ใช้แยกต่างหาก

หากพบว่าการทดสอบทำไม่ได้ ให้ระบุ Not Tested ตรง ๆ ห้ามรายงาน PASS แทน

## 8. Deliverables

เมื่อเสร็จให้ส่ง:

1. รายชื่อไฟล์ที่แก้ และ Unified Diff
2. สรุปบั๊กที่ยืนยันได้และสิ่งที่แก้
3. Prompt ก่อน/หลัง เฉพาะส่วนที่เปลี่ยน
4. ผล Static/Mock Tests
5. ผล LLM จริง พร้อม Raw Output ถ้ามี
6. Known Limitations และงานที่ยังต้องทดสอบบนเครื่องจริง
7. ไฟล์ Code ล่าสุดครบชุดที่เกี่ยวข้อง

**ไม่ต้อง Commit**

## FINAL PRINCIPLE

เป้าหมายไม่ใช่เพิ่มความยาว Prompt หรือจำนวนฟีเจอร์ แต่ทำให้ผลลัพธ์ **แม่นยำขึ้น มีประโยชน์ต่อการแต่งเพลงจริง และไม่ทำลายการควบคุมของผู้ใช้**

ให้แก้สาเหตุ ไม่ใช่อาการ หากพบข้อจำกัดของโมเดลหรือข้อมูลที่แก้ด้วย Code ไม่ได้ ให้รายงานข้อจำกัดนั้นอย่างชัดเจนแทนการสร้างผลลัพธ์ปลอม

ดำเนินการ Audit → Implement → Test → Review ภายในงานเดียว แล้วส่งกลับเพื่อตรวจ Code รอบสุดท้าย
