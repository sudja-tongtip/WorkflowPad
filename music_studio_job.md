# BRIEF — AI Music Director V3.1
## Creative Arrangement · Song Duration · Instrumental Planning · Cue Designer · Music Info Integration

**Project:** WorkflowPad Music Studio  
**Priority:** P0–P2  
**Mode:** Audit → Implement → Test → Compare → Promote if Passed

## 1. GOAL — เป้าหมายหลัก

ยกระดับ AI Music Director ให้เป็น **Creative Music Director & Arrangement Coach** ที่ออกแบบการผลิตเพลงจากเนื้อร้องและความต้องการของผู้ใช้จริง

ไม่ใช่เพียงสร้าง Music Caption และ Settings สามชุด

Director ต้องสามารถ:

1. วิเคราะห์อารมณ์ เนื้อหา และโครงสร้างเพลง
2. เสนอ Music Directions 3 แบบที่แตกต่างกันจริง
3. เลือก Genre, BPM, Key, Time Signature และ Vocal Character
4. คิดความยาวเพลงที่เหมาะสมด้วยตัวเอง
5. ออกแบบ Arrangement และจำนวน Bars ของแต่ละ Section
6. วาง Intro, Instrumental, Solo, Break, Transition และ Outro ตามความเหมาะสม
7. เสนอ Section Cues และ Vocal Cues
8. อธิบายเหตุผลและ Trade-off ของแต่ละแนวทาง
9. ตรวจความเป็นไปได้เมื่อเทียบกับเนื้อร้อง
10. เมื่อผู้ใช้เลือก Card ให้อัปเดต Music Caption และ Music Info ทั้งชุด
11. ให้ผู้ใช้ Preview และอนุมัติ Cue Changes แยกต่างหาก

### หลักการสำคัญ

**LLM คิดและออกแบบ → Python คำนวณและตรวจ → ผู้ใช้ตัดสินใจ**

Python ไม่ควรเป็นผู้กำหนดว่าเพลงควรยาวเท่าไรหรือบังคับให้ LLM เติม Bars ให้ครบเป้าหมาย

Director ต้องคิดแบบผู้กำกับดนตรี ไม่ใช่เครื่องคิดเลข

---

## 2. ใช้โครงสร้าง V3 เดิม

จาก `MUSIC_DIRECTOR_V3_REPORT.md` ระบบมีโครงสร้างที่ควร Reuse แล้ว:

- `music_studio/arrangement_plan.py`
- `music_studio/ai_music_director.py`
- `music_studio/prompts/candidates/ai_music_director_v3.md`
- `music_studio/prompts/specialists/ai_recommend.md`
- `music_studio/live_insights.py`
- `music_studio/cue_library.py`
- `music_studio/music_director_context.py`
- Music Director UI ใน `music_studio.py`
- Style/Options Settings Pipeline
- Cue Preview / Apply / Undo
- Numbered Clear Tags Parser
- Song / Sheet / Output Architecture

### สิ่งที่ V3 ทำได้แล้ว

- คำนวณ Duration จาก Bars/BPM ได้ถูกต้อง
- รองรับ 4/4, 3/4 และ 6/8 ตามนิยามของโปรแกรม
- ตรวจ Section Plan
- ตรวจ Cue จาก Catalog
- รองรับ Freeform Cue
- มี Cue Preview และ Apply
- มี Duration Confirmation
- แยก Style / Duration / Cue Selection
- รองรับ Legacy Directions

ให้รักษาส่วนที่ผ่านการทดสอบไว้

### สิ่งที่ V3 ยังมีปัญหา

1. LLM วางแผนเพลงสั้นกว่า Target มาก
2. Arrangement ของสาม Directions คล้ายกันเกินไป
3. LLM ไม่พิจารณาพื้นที่ดนตรีล้วนได้ดีพอ
4. Gemma ส่ง `none` เป็น Cue
5. Grok บางครั้งไม่มี `[DONE]`
6. Gemma ส่ง Closing Tags ปนใน Trade-off
7. Coaching ยังอธิบายการจัดเวลาไม่ดี
8. การเลือก Direction Card ยังไม่อัปเดต Music Info ครบชุด

ให้แก้ทั้งหมดในรอบนี้

---

# 3. CREATIVE DURATION PLANNING

## 3.1 LLM เป็นผู้ตัดสินความยาวเพลง

Director ต้องได้รับข้อมูล:

- Lyrics
- Section Structure
- จำนวนบรรทัดร้อง
- จำนวนพยางค์โดยประมาณ
- User Request
- Genre / Style
- Vocal Type
- Existing Cues
- Current BPM / Key / Time Signature
- Current Target Duration
- Existing Instrumental Sections

จากนั้น LLM ต้องคิดว่าเพลงนี้ควรมีความยาวประมาณเท่าไร

### Current Duration เป็น Preference

ตัวอย่าง:

Current Duration = 240 วินาที

Director อาจเสนอ:

- Direction A: 210 วินาที
- Direction B: 240 วินาที
- Direction C: 195 วินาที

ได้ทั้งหมด หากมีเหตุผลทางดนตรีรองรับ

ไม่จำเป็นต้องบังคับให้ครบ 240 วินาทีทุก Direction

แต่หากผู้ใช้ระบุชัดว่า Duration เป็นข้อจำกัดที่ต้องรักษา เช่น เพลงโฆษณา 30 วินาที ให้ถือเป็น Hard Constraint และ Director ต้องออกแบบภายในข้อจำกัดนั้น หรือแจ้งว่าไม่สามารถทำได้อย่างเหมาะสม

ต้องแยก:

- Preferred Duration
- Hard Duration Limit
- AI Recommended Duration

ห้ามตีความ Preferred Duration เป็น Hard Limit โดยอัตโนมัติ

---

## 3.2 ต้องคิดช่วงดนตรีด้วย

นี่คือข้อกำหนดสำคัญที่สุดของงานนี้

Director ต้องไม่คำนวณความยาวจากท่อนร้องเพียงอย่างเดียว

ให้พิจารณาช่วงดนตรี เช่น:

- Intro
- Instrumental Intro
- Instrumental Break
- Guitar Solo
- Synth Solo
- Drum Fill
- Build-up
- Breakdown
- Transition
- Post-Chorus
- Outro
- Instrumental Outro

Director สามารถเสนอช่วงดนตรีใหม่ตามความเหมาะสมกับ Genre และอารมณ์เพลง

แต่ไม่จำเป็นต้องเพิ่มทุกชนิด

### ตัวอย่าง

เพลง Electronic Action อาจเหมาะกับ:

- Synth Intro 8 Bars
- Verse 1 16 Bars
- Chorus 16 Bars
- Instrumental Chase 8 Bars
- Verse 2 16 Bars
- Bridge 8 Bars
- Final Chorus 16 Bars
- Outro 8 Bars

รวม 96 Bars

ที่ 128 BPM และ 4/4:

Planned Duration = 180 วินาที

นี่เป็นตัวอย่างการตัดสินใจทางดนตรี ไม่ใช่โครงสร้างที่ต้องใช้กับทุกเพลง

### Section ใหม่ที่ Director เสนอ

ต้องแยกสองประเภท:

**Existing Section**

ท่อนที่มีอยู่จริงใน Lyrics Snapshot

**Proposed Instrumental Section**

ท่อนดนตรีที่ Director คิดเพิ่ม แต่ยังไม่มีใน Lyrics

Director สามารถเสนอ Proposed Section ได้ แต่ห้ามเพิ่มลง Editor ทันที

หากเลือกใช้แผนที่มี Proposed Sections ให้แสดงชัดว่า Arrangement Plan นี้ต้องเพิ่มส่วนดนตรีใดบ้าง

เพิ่มได้ภายหลังผ่าน Preview / Apply ของระบบเดิม

ถ้ายังไม่เพิ่ม Proposed Sections ให้ถือว่า Duration เป็นเป้าหมายตามแผนที่เสนอ ไม่ใช่การยืนยันว่า Lyrics ปัจจุบันมีโครงสร้างครบตามนั้นแล้ว

---

# 4. PYTHON DURATION VALIDATION

## 4.1 Python ทำหน้าที่ตรวจคณิตศาสตร์

ใช้ `live_insights.seconds_per_bar` เดิม

ตัวอย่าง:

4/4:

`seconds = bars × 4 × 60 / BPM`

3/4:

`seconds = bars × 3 × 60 / BPM`

6/8:

ใช้ 2 Dotted-Quarter Pulses ต่อห้องตามนิยามโปรแกรม

ห้ามเขียนสูตร 6/8 แยกใหม่จนขัดกับ Live Insights

### Python ตรวจ

- จำนวน Bars เป็นจำนวนเต็มที่รองรับ
- BPM อยู่ในช่วงที่โปรแกรมรับได้
- Time Signature ถูกต้อง
- Section IDs ถูกต้อง
- Existing Sections ครบ
- Proposed Sections แยกจาก Existing Sections
- Total Bars ถูกต้อง
- Duration คำนวณถูก
- Planned Duration กับ Preferred Duration ต่างกันเท่าไร
- Lyrics Density มีความเสี่ยงหรือไม่
- Duration อยู่ในช่วงที่ ACE-Step/โปรแกรมรองรับหรือไม่

ห้ามใช้จำนวนวินาทีที่ LLM เขียนเองเป็นค่าจริงโดยไม่ตรวจ

---

## 4.2 Duration Coach

เมื่อแผนต่างจาก Preferred Duration ต้องอธิบายเหตุผล

ตัวอย่าง:

**Director Recommendation**

"แนะนำเพลงยาวประมาณ 3:15 เพราะต้องการเว้นพื้นที่ Instrumental Chase ก่อน Final Chorus เพื่อสร้างความรู้สึกเหมือนฉากไล่ล่า"

**Trade-off**

"เพลงมีพื้นที่สร้างบรรยากาศมากขึ้น แต่ Hook รอบสุดท้ายจะมาช้ากว่าเวอร์ชันที่กระชับ"

ถ้า LLM เสนอ Duration ที่ไม่เหมาะกับ Lyrics หรือแผนจำนวนห้อง ให้ Python แจ้ง Warning และแสดงข้อมูลที่ไม่สอดคล้อง

ไม่ต้องให้ Python เติม Bars หรือแก้แผนของ LLM เงียบ ๆ

### การประเมินความเป็นไปได้

แสดง:

- Planned Arrangement Duration
- Preferred Duration Difference
- Estimated Lyrics Feasibility
- AI Generation Risk

สามเรื่องนี้ต้องแยกกัน

ห้ามสรุปว่า Planned Duration คือเวลาของเสียงที่ ACE-Step จะสร้างจริงแน่นอน

---

# 5. THREE MUSIC DIRECTIONS

หนึ่งการกด Analyze ให้ Director เสนอ 3 Directions ตามเดิม

แต่ละ Direction ต้องประกอบด้วย:

1. Title
2. Musical Vision
3. Genre / Mood
4. BPM
5. Key
6. Time Signature
7. Vocal Character
8. Energy
9. Music Caption
10. Recommended Duration
11. Arrangement / Bars Plan
12. Instrumental Plan
13. Section & Vocal Cue Plan
14. Coaching Reasons
15. Trade-offs

### ความแตกต่างที่ต้องการ

สาม Directions ต้องไม่เหมือนกันโดยเปลี่ยนแค่ BPM หรือชื่อ

ต้องแตกต่างอย่างมีสาระ เช่น:

**A — Aggressive Synthwave Action**

จังหวะกระชับ Synth หนัก Verse สั้นและคึกคัก

**B — Cinematic Electronic Action**

เน้น Atmosphere, Build-up, Instrumental Chase และ Climax

**C — Industrial Electronic Drive**

ใช้จังหวะหนัก Bass ดุดัน Guitar Texture และ Groove ต่อเนื่อง

นี่เป็นเพียงตัวอย่าง ไม่ให้ Hardcode

Director ต้องตัดสินจาก User Request และเนื้อเพลงจริง

---

# 6. SECTION & VOCAL CUE DESIGNER

## 6.1 Cue ต้องมาจากเหตุผลทางดนตรี

Director ควรเสนอ:

- Section Energy
- Instrumentation
- Arrangement Style
- Vocal Delivery
- Build-up / Breakdown
- Transition
- Solo / Instrumental Cue

ตัวอย่าง:

`[Intro - synth]`

`[Verse 1 - spoken word]`

`[Chorus - anthemic]`

`[Bridge - whispered]`

`[Outro - fade out]`

ตัวอย่างต้องผ่าน Cue Catalog จริงก่อนถือว่า Valid

หากไม่มี Cue ที่เหมาะ ไม่ต้องเสนอ

### Existing Cues

Director ต้องอ่าน Cue เดิมก่อน

Action:

- KEEP
- ADD
- REPLACE
- NO SUGGESTION

ห้ามเพิ่ม Cue ซ้ำกับของเดิม

ห้ามเปลี่ยน Cue ที่ผู้ใช้เขียนไว้โดยอัตโนมัติ

---

## 6.2 แก้ Bug Cue `none`

ค่าต่อไปนี้ต้องถือว่าไม่มีข้อเสนอ:

- `none`
- `null`
- `-`
- ค่าว่าง
- Placeholder ที่ไม่มีความหมายเป็น Cue

ต้องไม่ถูกแปลงเป็น Freeform

ห้ามแสดงเป็น Cue ที่ Apply ได้

ส่วน Freeform ที่มีความหมายจริงยังรองรับ แต่ต้องมีสถานะแยกจาก Catalog

---

## 6.3 Cue Safety

ตรวจ:

- Cue Category
- Section Type
- Duplicate
- Conflict
- Existing Cue
- MAX_CUES
- Unsupported Cue
- Invalid Section
- Snapshot Staleness

ถ้าเกิน MAX_CUES ต้องไม่ Apply จนเกินข้อจำกัดที่กำหนด

ห้ามลบ Cue เดิมเพื่อเพิ่ม Cue ใหม่โดยไม่ขออนุมัติ

### Apply

Cue ต้องผ่าน:

Preview → Select → Apply → Undo/Redo

การเลือก Music Direction Card ไม่แทรก Cue ลง Lyrics ทันที

รักษา Lossless Lyrics Formatting Policy เดิม

---

# 7. MUSIC DIRECTION CARD → MUSIC INFO

**ส่วนนี้เป็นข้อกำหนดใหม่ที่สำคัญ**

เมื่อผู้ใช้เลือก Music Direction Card ให้ใช้ค่าดนตรีทั้งชุด ไม่ใช่เพียง Music Caption

## 7.1 ค่าที่ต้องอัปเดต

เมื่อเลือก Card:

- Music Caption / Style
- Genre
- Mood
- Energy
- Vocal Settings ที่รองรับ
- BPM
- Key
- Time Signature
- Duration

ใช้ Settings ของ Direction ที่เลือกเท่านั้น

### ตัวอย่าง

Current Music Info:

    BPM:            130
    Key:            D minor
    Time Signature: 4/4
    Duration:       240 seconds
    Language:       Thai (th)
    Seed:           -1

Direction Card เสนอ:

    BPM:            128
    Key:            E minor
    Time Signature: 4/4
    Duration:       195 seconds

หลังเลือก Card และ Apply สำเร็จ:

    BPM:            128
    Key:            E minor
    Time Signature: 4/4
    Duration:       195 seconds
    Language:       Thai (th)
    Seed:           -1

**Language และ Seed ต้องคงเดิม**

Duration = 195 ต้องเป็นค่าที่ตรวจสอบจาก Arrangement Plan โดย Python แล้ว ไม่ใช่ตัวเลขที่ LLM เขียนมาโดยไม่มีที่มา

---

## 7.2 Card Selection Behavior

เปลี่ยนพฤติกรรมจาก V3 เดิม:

**Click Direction Card = Apply Complete Music Direction**

ใช้ค่า:

- Caption
- Style
- BPM
- Key
- Time Signature
- Duration
- Vocal/Genre/Energy Settings ที่รองรับ

การ Apply ต้องเป็นงานเดียวกันใน Settings Pipeline เดิม

ห้ามเกิดการเปลี่ยนค่าครึ่งชุด

หาก Validation ล้มเหลว ให้คง Settings เดิมไว้และแจ้งเหตุผล

### Legacy Direction

หากการ์ดเก่าไม่มี Duration Plan ให้รักษาพฤติกรรม Style Selection เดิมไว้

แสดงชัดว่า Duration ไม่ได้เปลี่ยนเพราะไม่มีแผนที่ตรวจสอบได้

ห้ามสร้าง Duration สมมติให้ Legacy Card

### V3.1 Direction ที่ไม่มี Duration ถูกต้อง

ห้ามแสดงว่าการใช้ Music Plan ทั้งชุดสำเร็จ

ต้องแจ้งว่าข้อมูลแผนยังไม่สมบูรณ์ และให้ผู้ใช้เลือกใช้เฉพาะ Style ได้ผ่าน Action ที่แยกชัดเจน

---

## 7.3 เมื่อแก้ Settings เองภายหลัง

หากผู้ใช้เลือก Card แล้วปรับ BPM, Time Signature หรือ Duration เอง:

- UI ต้องแสดงค่าที่ผู้ใช้แก้ล่าสุด
- Recalculate ระยะเวลาตาม Bars เพื่อประเมินความสอดคล้อง
- ไม่เขียนทับ Duration ที่ผู้ใช้ตั้งเอง
- หากแผนเดิมไม่สอดคล้อง ให้แสดง Warning
- ไม่เปลี่ยน Cue อัตโนมัติ
- ไม่เรียก Director LLM ใหม่

ผู้ใช้ยังสามารถปรับแต่ละค่าได้อย่างอิสระ

---

## 7.4 การเลือกข้อมูลข้าม Direction

รองรับการเลือกแยกในโหมด Advanced เช่น:

- Style จาก Direction 1
- Duration จาก Direction 2
- Cues จาก Direction 3

แต่ต้องตรวจความสอดคล้อง

หากผสม BPM และ Duration คนละ Direction ให้คำนวณผลใหม่และแสดงคำเตือน

ห้ามนำค่า Duration ที่คำนวณภายใต้ BPM หนึ่งไปแสดงเหมือนเป็นแผนที่ถูกต้องของอีก BPM

---

# 8. TEXT-BASED UI MOCKUP

ตัวอย่างเป็นแผนสมมติ ไม่ใช่ผลวิเคราะห์เพลงจริง

## 8.1 Music Direction Card

    ┌────────────────────────────────────────────────────────────┐
    │  02 · CINEMATIC ELECTRONIC ACTION                         │
    │                                                            │
    │  DIRECTOR'S VISION                                        │
    │  Electronic Action ที่สร้างบรรยากาศแบบภาพยนตร์             │
    │  เริ่มด้วย Synth Atmosphere แล้วเพิ่มพลังถึง Final Chorus │
    │                                                            │
    │  MUSIC SETTINGS                                           │
    │                                                            │
    │  Genre: Electronic / Cinematic                            │
    │  Mood: Dark / Energetic                                   │
    │  Vocal: Male                                              │
    │  BPM: 128                                                 │
    │  Key: E minor                                             │
    │  Time Signature: 4/4                                      │
    │  Energy: High                                             │
    │                                                            │
    │  RECOMMENDED DURATION                                     │
    │                                                            │
    │  3:00 · 96 Bars                                           │
    │                                                            │
    │  [ ใช้ Music Direction นี้ ]                              │
    └────────────────────────────────────────────────────────────┘

## 8.2 Arrangement & Instrumental Plan

    ┌────────────────────────────────────────────────────────────┐
    │  ARRANGEMENT PLAN                                        │
    │                                                            │
    │  Intro / Synth Atmosphere     8 Bars     0:15             │
    │  Verse 1                     16 Bars     0:30             │
    │  Chorus                      16 Bars     0:30             │
    │  Instrumental Chase           8 Bars     0:15             │
    │  Verse 2                     16 Bars     0:30             │
    │  Bridge                       8 Bars     0:15             │
    │  Final Chorus                16 Bars     0:30             │
    │  Cinematic Outro              8 Bars     0:15             │
    │                                                            │
    │  TOTAL                       96 Bars     3:00             │
    │                                                            │
    │  Original Target: 4:00                                    │
    │  Director Recommends: 3:00                                │
    │                                                            │
    │  WHY                                                     │
    │  เนื้อเพลงเหมาะกับโครงสร้างที่กระชับและมีช่วง              │
    │  Instrumental Chase ก่อนเข้าสู่ท่อนที่สอง                 │
    │                                                            │
    │  TRADE-OFF                                               │
    │  เพลงเข้มข้นขึ้นและลดช่วงที่อาจยืดเยื้อ                   │
    │  แต่มีพื้นที่ดนตรีน้อยกว่าแผน 4 นาที                     │
    └────────────────────────────────────────────────────────────┘

Instrumental Chase เป็น Proposed Section หากยังไม่มีใน Lyrics ต้องแสดงว่าเป็นข้อเสนอ ไม่ใช่ท่อนที่เพิ่มแล้ว

## 8.3 Music Info หลังเลือก Card

    ┌────────────────────────────────────────────────────────────┐
    │  ข้อมูลเพลง                                               │
    │                                                            │
    │  BPM              Key             Time Signature          │
    │  128              E minor         4/4                     │
    │                                                            │
    │  Duration         Language        Seed                    │
    │  180              Thai (th)       -1                      │
    │                                                            │
    │  Music Caption และ Style ใช้จาก Direction 02 แล้ว         │
    │                                                            │
    │  Cue Suggestions ยังรอ Preview / Apply                    │
    └────────────────────────────────────────────────────────────┘

## 8.4 Cue Preview

    ┌────────────────────────────────────────────────────────────┐
    │  SECTION & VOCAL CUE SUGGESTIONS                         │
    │                                                            │
    │  Intro                                                    │
    │  [+] [Intro - synth]                                      │
    │  เหตุผล: เปิดด้วย Synth Motif                             │
    │                                                            │
    │  Verse 1                                                  │
    │  [+] [Verse 1 - spoken word]                              │
    │  เหตุผล: Vocal Delivery กระชับ                            │
    │                                                            │
    │  Chorus                                                   │
    │  [+] [Chorus - anthemic]                                  │
    │  เหตุผล: ขยาย Hook                                       │
    │                                                            │
    │  Bridge                                                   │
    │  [=] [Bridge - whispered]                                 │
    │  KEEP — Cue เดิมทำหน้าที่ดีอยู่แล้ว                        │
    │                                                            │
    │  [ Preview Selected Changes ]                             │
    │  [ Apply Cues ]                                           │
    └────────────────────────────────────────────────────────────┘

ไม่ Apply Cue เมื่อผู้ใช้คลิก Direction Card

---

# 9. NUMBERED CLEAR TAGS CONTRACT

รักษามาตรฐาน Numbered Clear Tags

ตรวจ Prompt / Parser เดิมก่อนเปลี่ยนรูปแบบ

แต่ละ Direction ต้องส่ง:

- Direction Name
- Vision
- Caption
- Settings
- Arrangement Plan
- Existing Section References
- Proposed Instrumental Sections
- Cue Suggestions
- Coaching Reason
- Trade-off

Python เติม:

- Total Bars
- Duration Seconds
- Duration Display
- Preferred Duration Difference
- Lyrics Density Risk
- Validation Status

### Format Reliability

ต้องแก้ปัญหา:

- Grok ไม่มี `[DONE]`
- Gemma ส่ง `[/STYLE.1]` ปนใน Trade-off
- Cue Placeholder
- Missing Section Plan
- Partial Direction
- Duplicate Direction
- Invalid Settings
- Invalid Bars

Parser ต้องไม่รับ Control Tags เป็นเนื้อหาปกติ

หาก Direction หนึ่งผิด ให้กู้เฉพาะ Directions ที่ถูกต้องตาม Contract

ห้ามสร้างค่าที่ขาดขึ้นมาเอง

ห้ามทำให้ Legacy AI Recommend Output ใช้งานไม่ได้

---

# 10. PROMPT CANDIDATE V3.1

สร้าง Candidate ใหม่:

`music_studio/prompts/candidates/ai_music_director_v3_1.md`

ใช้ Candidate V3 เป็นฐาน

### Prompt Direction

ต้องสอนให้ LLM:

1. อ่านเจตนาของผู้ใช้ก่อนออกแบบ
2. วิเคราะห์โครงเพลงและเนื้อร้อง
3. คิดแนวเพลงและการเรียบเรียง
4. เลือก BPM / Key / Time Signature
5. ตัดสินความยาวที่เหมาะสม
6. พิจารณาช่วงดนตรีล้วน
7. กระจาย Bars ให้มีเหตุผล
8. เสนอ Instrumental Sections ที่เหมาะสม
9. เสนอ Cue ที่เกี่ยวข้องกับ Arrangement
10. อธิบายเหตุผลและ Trade-off
11. รักษา Existing Lyrics และ Cues
12. แสดงผลตาม Clear Tags Contract

### Duration Guidance

ต้องไม่สั่ง LLM ว่า:

"Make the total bars match the target exactly."

ให้ใช้แนวทาง:

"Consider the user's preferred duration, lyrical content, musical structure and instrumental development. Recommend a musically appropriate arrangement and duration. Explain meaningful deviations from the user's preference."

ไม่ต้องส่งตัวเลขวินาทีให้ LLM คำนวณเอง

ส่ง BPM/Time Signature/Target เป็นข้อมูลประกอบได้ แต่ให้ Python ตรวจเวลาหลังจากโมเดลเสนอ Bars

### Few-shot

ใช้ตัวอย่างสั้น:

- เพลงที่ควรสั้นกว่า Target
- เพลงที่ควรยาวกว่า Target เพราะ Instrumental
- เพลงที่ควรมี Solo
- เพลงที่ไม่มีเหตุผลให้เพิ่ม Instrumental
- เพลงที่ Existing Cue ควร Keep

อย่าใช้ Prompt ยาวเกินจำเป็นหรือเพิ่มกฎซ้ำซ้อนจำนวนมาก

---

# 11. TESTING PLAN

## 11.1 Duration & Arrangement

ทดสอบ:

- 4/4
- 3/4
- 6/8
- Different BPM
- Preferred Duration
- Hard Duration Limit
- AI Recommended Duration
- Existing Sections
- Proposed Instrumental Sections
- Repeated Sections
- Instrumental Intro / Outro
- Total Bars
- Planned Duration
- Lyrics Density
- Duration Outside Supported Range
- No Silent Bar Filling

ต้องตรวจตัวเลขกับค่าที่ทราบคำตอบแน่นอน

## 11.2 Cue Safety

ทดสอบ:

- Catalog Cue
- Studio Cue
- Freeform Cue
- `none` / `null` / `-`
- Duplicate
- Existing Cue
- Conflict
- MAX_CUES
- Preview
- Apply
- Undo/Redo
- Cancel
- Lossless Lyrics
- Proposed Section ยังไม่ถูกเพิ่ม
- Snapshot เปลี่ยนหลังวิเคราะห์

## 11.3 Card → Music Info

ทดสอบ:

1. Card 1 อัปเดต Settings ของ Card 1 ทั้งชุด
2. Card 2 อัปเดต Settings ของ Card 2 ทั้งชุด
3. BPM ถูกต้อง
4. Key ถูกต้อง
5. Time Signature ถูกต้อง
6. Duration มาจาก Python
7. Caption / Style ถูกต้อง
8. Genre / Mood / Vocal Settings ถูกต้อง
9. Language ไม่เปลี่ยน
10. Seed ไม่เปลี่ยน
11. Cue ไม่ถูก Apply อัตโนมัติ
12. ไม่ Auto Generate
13. Validation Fail ไม่ทำให้ Settings เปลี่ยนครึ่งชุด
14. Song/Sheet ไม่ปะปน
15. Legacy Card ยังใช้ได้
16. เปลี่ยน BPM เองแล้วมี Recalculation
17. เลือกข้าม Direction มี Warning
18. เปิด Editor อีกครั้งแล้ว Settings ยังถูกต้อง

## 11.4 LLM / Parser

ทดสอบ:

- Grok
- Local Gemma
- `[STYLE.1]` ถึง `[STYLE.3]`
- `[DONE]`
- Missing Tags
- Closing Tag Leakage
- Invalid Bars
- Invalid Duration
- Invalid Cue
- Invalid Section
- Missing Direction
- Partial Recovery
- No Cross-Specialist Calls
- One Director LLM Request
- No Auto Analyze

## 11.5 Regression

ตรวจ:

- AI Recommend V2
- Director V3
- Basic / Advanced Style
- Generate Song
- Lyrics Editor
- Section Cue Formatter
- Preview / Apply / Undo
- Song Switching
- Sheet Switching
- Save / Restore
- My Music
- Output History

ใช้ Temporary APPDATA / LOCALAPPDATA / USERPROFILE / HOME ก่อน Import โมดูล

ห้ามให้ Tests แตะ AppData จริง

---

# 12. REAL MODEL TESTS

ทดสอบ Production เทียบ Candidate V3.1

Backend:

- Grok
- Local Gemma

เริ่มเพลงเดียวก่อน และขยายได้ไม่เกิน 3 เพลง

### Case 1 — Electronic Action

เพลงที่ต้องมี Arrangement และ Instrumental Development

ตรวจว่า Director สร้างสาม Directions ที่แตกต่างกันจริง

### Case 2 — Dense Lyrics

เพลงที่เนื้อร้องแน่น

ตรวจว่า Director ออกแบบพื้นที่ร้องได้โดยไม่ตัด Lyrics

### Case 3 — Existing Cues

เพลงที่มี Cues เดิมและมี Instrumental Sections

ตรวจว่า Director รักษา Existing Cues และเลือก Keep/Add/Replace อย่างมีเหตุผล

### Evaluation

- Creative Direction Quality
- Arrangement Coherence
- Duration Reasoning
- Instrumental Planning
- Cue Relevance
- Cue Validity
- Coaching Quality
- Format Stability
- Card / Music Info Integration
- Cost / Latency

ทดสอบด้วย Runtime Parameters เดียวกับที่แอปใช้งานจริง หรือรายงานความแตกต่างอย่างชัดเจน

ไม่ใช้ LLM Judge เพียงอย่างเดียว

---

# 13. PROMOTION GATE

**Gate 1 — Arrangement Quality**

Director วางโครงสร้างดนตรีได้สมเหตุสมผล รวมถึงพื้นที่ดนตรีล้วน

**Gate 2 — Duration Reliability**

LLM เสนอแผนได้ และ Python ตรวจตัวเลขถูกต้อง ไม่บังคับให้ตรง Preferred Duration โดยไม่มีเหตุผล

**Gate 3 — Cue Safety**

ไม่มี Placeholder ที่ Apply ได้ และไม่แก้ Existing Cue อัตโนมัติ

**Gate 4 — Music Direction Diversity**

สาม Directions แตกต่างกันจริงในเชิงดนตรี

**Gate 5 — Coaching Quality**

Director อธิบายเหตุผลและ Trade-off ได้

**Gate 6 — Format Stability**

Grok/Gemma ใช้ Clear Tags ได้อย่างเสถียร

**Gate 7 — Music Info Integration**

เลือก Card แล้ว Caption, BPM, Key, Time Signature, Duration และ Settings ที่เกี่ยวข้องเปลี่ยนครบอย่างถูกต้อง

**Gate 8 — Regression**

ระบบเดิมยังทำงานครบ ไม่มี Auto Apply, Auto Generate หรือข้อมูลปะปน

ถ้า Candidate ไม่ผ่าน ให้คง Production Prompt และ Contract เดิม

ส่วน Duration Calculator, Cue Validator และ Card Integration ที่ผ่าน Tests สามารถเก็บไว้ได้ โดยต้องไม่ทำให้ Legacy Directions เสีย

---

# 14. ข้อห้าม

- ไม่เปลี่ยน LLM Model
- ไม่ Patch ComfyUI
- ไม่แก้ ACE-Step Engine
- ไม่เพิ่ม LLM Request ใน Runtime
- ไม่เรียก Specialist ตัวอื่นอัตโนมัติ
- ไม่แก้ Lyrics อัตโนมัติ
- ไม่เพิ่ม Instrumental Sections ลง Editor อัตโนมัติ
- ไม่ Apply Cue อัตโนมัติ
- ไม่เขียนทับ AppData Prompt ที่ผู้ใช้แก้ไว้
- ไม่ลบ Generated Songs หรือ Output History
- ไม่เปลี่ยน Song ID / Sheet ID Architecture
- ไม่สร้าง Settings Store อีกชุด
- ไม่ Commit โดยไม่ได้รับอนุญาต

รักษา Session Policy, Prompt Versioning และระบบ Undo/Redo เดิม

---

# 15. DELIVERABLES

สร้างรายงาน:

**MUSIC_DIRECTOR_V3_1_REPORT.md**

ต้องมี:

1. Current Architecture Audit
2. V3 Root Causes
3. Files Changed
4. Creative Duration Design
5. Existing / Proposed Section Handling
6. Duration Calculation Contract
7. Cue Validation Fixes
8. Director Output Contract
9. Card → Music Info Integration
10. UI Comparison กับ Mockup
11. Candidate V3.1 Prompt
12. Production vs Candidate
13. Grok/Gemma Actual Outputs
14. Duration / Cue / Parser Tests
15. Card / Settings Regression
16. Runtime Parameters
17. Request Count / Latency
18. Promotion Gate Results
19. Known Limitations
20. Promote หรือ Keep Production Decision

ให้ AI Coder ตรวจโค้ดจริง แก้ไข ทดสอบ และรายงานผลภายในงานเดียวกัน ไม่หยุดเพียงส่งแผน

หากพบ Bug เพิ่มในเส้นทาง Director → Arrangement → Settings → Cue ให้แก้ในขอบเขตนี้พร้อม Tests

## FINAL GOAL

AI Music Director ต้องสามารถบอกผู้ใช้ได้ว่า:

"ผมแนะนำเพลง Electronic Action ความยาวประมาณ 3 นาที ที่ 128 BPM ในจังหวะ 4/4 โดยใช้ Synth Intro สร้างบรรยากาศ ก่อนเข้าสู่ Verse ที่กระชับ มี Instrumental Chase คั่นระหว่างท่อน และ Final Chorus เป็นจุดสูงสุดของเพลง

แผนมี 96 ห้อง และ Python คำนวณได้ 180 วินาที พร้อม Cue ที่เสนอให้เลือกใช้ หากคุณต้องการเพลงยาวกว่านี้ สามารถขยาย Instrumental หรือ Outro ได้ แต่จะทำให้โครงสร้างเพลงผ่อนลง"

เมื่อผู้ใช้เลือก Direction Card:

**Music Caption + BPM + Key + Time Signature + Duration + Music Settings ต้องอัปเดตในแผงข้อมูลเพลงทันที**

ส่วน Cue และ Instrumental Sections ที่เสนอใหม่ยังต้องรอ Preview/Apply จากผู้ใช้

**เป้าหมายคือให้ Director เป็นผู้กำกับดนตรีที่คิดสร้างสรรค์ วางแผนเพลงได้จริง และช่วยให้ผู้ใช้ตัดสินใจ ไม่ใช่ AI ที่แค่สร้าง Caption หรือเติม Bars ให้ครบเวลา**
