# AI Coder Brief — Implement Cover + Repaint (ACE-Step 1.5)

## เป้าหมาย

เพิ่มระบบ **Cover Generation** และ **Audio Repaint** ใน WorkflowPad Music Studio โดยใช้ Kaola ACE-Step 1.5 Custom Nodes และ Workflow JSON ที่เตรียมไว้แล้ว

ต้องทำให้ใช้งานได้จริงตั้งแต่ UI → ComfyUI Queue → Audio Output → Results → Project Persistence

**ทำงานให้จบในรอบเดียว** ตรวจสอบโค้ดปัจจุบันก่อน แล้ว Implement, Verify และแก้ปัญหาที่พบต่อเนื่อง โดยไม่ต้องส่งแผนกลับมารออนุมัติทุกขั้น

### ข้อกำหนดหลัก

1. ห้ามแก้พฤติกรรม Normal Generate และ Workflow เดิม
2. ห้ามเขียนทับ Source Take หรือ Take ที่มีอยู่
3. ใช้ Results, Queue, Project Save และ Audio Player เดิมให้มากที่สุด
4. ไม่เพิ่มหน้าต่างหรือหน้าใหม่ที่ไม่จำเป็น
5. ห้ามสร้างระบบ Lyrics Alignment
6. Repaint ใช้ Caption ไม่ต้องมี Lyrics Editor
7. ห้ามทดลอง Generate เสียงจริงโดยไม่ได้รับอนุญาต
8. ห้ามติดตั้งหรือเปลี่ยน Dependencies, Models หรือ Custom Nodes โดยพลการ
9. ห้ามเปลี่ยนโครงสร้างระบบอื่นที่ไม่เกี่ยวข้อง
10. ต้องรองรับโปรเจกต์เก่าและข้อมูล Take เดิม

---

## Phase 1 — Audit โค้ดและ Workflow ก่อนแก้

ตรวจสอบ Implementation ปัจจุบันของ:

- Song Settings / Style / Options
- Normal Generate และ ComfyUI Workflow Submission
- Editor Tabs
- Results และ Take Management
- Project / Song / Take ID
- Project Save / Load / Restart
- Audio Playback
- Background Tasks และ UI Update
- Dependency และ Audio Processing ที่ติดตั้งอยู่แล้ว

ตรวจสอบ Workflow JSON:

**Cover:** `audio_cover_generation.json`

**Repaint:** `audio_repaint.json`

ตรวจสอบ Node Inputs และ Outputs จริงจาก Kaola โดยเฉพาะ:

- `ACE_STEP_Cover`
- `ACE_STEP_Repaint`
- `LoadAudio`
- `SaveAudio`
- `PreviewAudio`

ตรวจว่า Kaola ติดตั้งอยู่แล้วหรือไม่ และ ComfyUI สามารถรู้จัก Node ทั้งสองได้จริงหรือไม่

ตรวจโมเดลที่ Workflow ต้องการ:

- `checkpoint_dir`
- `config_path`
- `lm_model_path`
- `device`
- `quantization`
- `inference_steps`

ห้ามสมมติว่าโมเดล Native ACE-Step 1.5 เดิมใช้แทน Kaola ได้ทันที

**ถ้าพบว่าขาด Custom Node, Model หรือ Dependency ให้รายงานรายการที่ขาดอย่างชัดเจน ห้ามติดตั้งเอง แต่ยังสามารถทำส่วน UI และ Integration ที่ตรวจสอบได้ต่อไป**

---

## Phase 2 — Cover ใน Song Settings

เพิ่ม Cover Section ขนาดเล็กใน Song Settings เดิม

### UI

- Checkbox: `Enable Cover`
- Radio: `External`
- Radio: `This Project`

เมื่อเลือก External:

- เปิด File Picker เพื่อเลือกไฟล์เสียงต้นฉบับ
- แสดงชื่อไฟล์ที่เลือก
- ตรวจสอบว่าไฟล์ยังมีอยู่ก่อน Generate

เมื่อเลือก This Project:

- แสดง Song Selector ของโปรเจกต์ที่เปิดอยู่
- แสดง Take Selector ของ Song ที่เลือก
- ใช้เฉพาะ Takes ที่มีไฟล์เสียงจริง
- ไม่แสดงเพลงจากโปรเจกต์อื่น

### Behavior

เมื่อ Enable Cover = OFF:

- ใช้ Normal Generate เดิม 100%
- ไม่ส่งค่า Cover เข้า Workflow ปกติ
- จำ Source Selection ไว้สำหรับเปิดใช้อีกครั้ง

เมื่อ Enable Cover = ON:

- Validate Source Audio
- โหลด Workflow Cover
- ส่ง Audio, Caption และ Lyrics ของ Song ปัจจุบัน
- ส่งค่าที่รองรับจาก Song Settings ตามความเหมาะสม
- ใช้ค่า Default ของ Workflow สำหรับพารามิเตอร์ที่ไม่ได้เปิดให้ปรับใน UI
- ไม่เพิ่ม Advanced Settings จำนวนมาก

**Destination ต้องเป็น Song ที่กำลังเปิดใน Editor ไม่ใช่ Song ที่เลือกเป็น Source**

ผลลัพธ์ต้องบันทึกเป็น Take ใหม่ของ Destination Song

---

## Phase 3 — Repaint Tab ใหม่

เพิ่มหรือปรับ Repaint Tab ภายใน Editor โดยใช้ Tab เดียว ไม่สร้าง Repaint Window แยก

### Layout

เรียงจากบนลงล่าง:

1. Source Song / Take Selector
2. Audio Playback
3. Real Waveform
4. Start / End Selection
5. Repaint Caption
6. Repaint Button

ไม่ต้องมี Lyrics Editor ใน Repaint

### Source Selection

รองรับสองทาง:

**A. เปิดจาก Results**

- ผู้ใช้กด `Repaint` บน Take
- เปิด Repaint Tab
- เลือก Source Take นั้นอัตโนมัติ
- โหลดเสียงและ Draft ของ Take นั้น

**B. เลือกใน Repaint Tab**

- เลือก Song จากโปรเจกต์ปัจจุบัน
- เลือก Take ของ Song นั้น
- โหลดเสียงของ Take ที่เลือก

ห้ามเลือก Source ข้ามโปรเจกต์ใน Phase นี้

### Repaint Caption

ใช้ Multiline Text Field สำหรับอธิบายเสียงที่ต้องการสร้างใหม่

ตัวอย่าง:

"Replace this section with an expressive electric guitar solo while preserving the original tempo and musical atmosphere."

ส่งค่าไปยัง `caption` ของ Kaola Repaint โดยตรง

ไม่ต้องเพิ่มช่อง Lyrics และไม่ต้องนำ Lyrics ไปต่อท้าย Caption อัตโนมัติ

---

## Phase 4 — Real Audio Waveform

ต้องแสดง Waveform จากไฟล์เสียงจริงของ Source Take ไม่ใช่กราฟสุ่มหรือ Placeholder ถาวร

### Audio Processing

เลือกใช้ Library ที่เหมาะสมจาก Environment ปัจจุบัน:

- `soundfile` สำหรับ WAV/FLAC
- `numpy` สำหรับประมวลผล Waveform
- `ffmpeg` สำหรับรูปแบบที่ต้องถอดรหัสเพิ่มเติม

ตรวจ Dependencies ที่มีอยู่ก่อน หลีกเลี่ยงการเพิ่ม Library ใหม่โดยไม่จำเป็น

### Waveform Algorithm

1. อ่านไฟล์เสียงใน Background ไม่บล็อก Flet UI
2. รองรับ Mono และ Stereo
3. แปลงข้อมูลเสียงเป็น Min/Max Peaks ต่อช่วงเวลา
4. สร้าง Waveform Data ที่มีขนาดเหมาะสมกับความกว้างของ UI
5. Cache ตาม Source Audio และ File Identity
6. เปิด Take เดิมซ้ำต้องใช้ Cache ได้
7. เปลี่ยน Selection ไม่ต้อง Decode เสียงใหม่
8. ยกเลิกหรือเพิกเฉยต่อผลโหลดเก่าเมื่อผู้ใช้เปลี่ยน Take ระหว่างโหลด

ไม่จำเป็นต้องโหลด Audio Samples ทั้งเพลงเข้า UI

### Waveform Interaction

- Drag เพื่อเลือกช่วงเสียง
- แสดง Selection Overlay
- ปรับ Start / End เป็นวินาทีได้
- Zoom In / Out
- Play / Pause
- Seek
- ฟังช่วงที่เลือกได้
- ถ้าระบบ Audio Player เดิมรองรับ ให้ Reuse

Selection ต้องสัมพันธ์กับ Duration ของไฟล์จริง

### Validation

ต้องตรวจว่า:

- Start ≥ 0
- End > Start
- End ≤ Audio Duration
- Source Audio ยังมีอยู่
- Caption ไม่ว่าง

ตรวจเพิ่มเติมตามข้อจำกัดของ Kaola ที่พบจากโค้ดจริง

ห้ามส่งช่วงเวลาที่ไม่ถูกต้องเข้า Queue

---

## Phase 5 — Kaola Workflow Integration

สร้าง Integration สำหรับ Cover และ Repaint แยกจาก Normal Generate

สามารถ Reuse Queue Infrastructure เดิมได้ แต่ต้องไม่แก้ Workflow Generate ปกติ

### Cover

ใช้ `audio_cover_generation.json`

ส่งค่าที่จำเป็น:

- Source Audio
- Caption
- Lyrics
- Audio Cover Strength ตาม Default
- Parameters ที่รองรับจาก Song Settings

ตรวจชนิดข้อมูลและการอ้างอิง Node ตาม Workflow จริง

### Repaint

ใช้ `audio_repaint.json`

ส่งค่า:

- `src_audio`
- `caption`
- `repainting_start`
- `repainting_end`

ค่า Start / End ต้องเป็นวินาทีตาม Workflow Kaola

### Workflow Handling

- ใช้ Node ID / Class Type ที่ตรวจสอบจาก JSON จริง
- ห้าม Hardcode โครงสร้าง Workflow โดยไม่ตรวจสอบ
- รองรับการส่งไฟล์ Source Audio ให้ ComfyUI อย่างถูกต้อง
- รองรับกรณี ComfyUI รันคนละ Process
- ใช้ API Workflow Format ที่ ComfyUI รับได้
- ตรวจ Queue Status / Completion / Error
- ดึงไฟล์ Audio Output กลับมาอย่างถูกต้อง

**สำคัญ:** Repaint JSON ใช้ `PreviewAudio` ส่วน Cover JSON ใช้ `SaveAudio` ที่อาจเป็น Deprecated

ต้องตรวจวิธีดึง Output จริงจาก ComfyUI History และ Output Metadata ห้ามสมมติว่าไฟล์อยู่ใน Output Directory เสมอ

ถ้าจำเป็นให้ปรับเฉพาะสำเนา Workflow Cover/Repaint เพื่อให้บันทึก Output ได้เสถียร โดยไม่แตะ Workflow Generate เดิม

---

## Phase 6 — Results และ Take Management

เมื่อ Cover หรือ Repaint สำเร็จ:

- สร้าง Take ID ใหม่
- บันทึก Audio File เข้าโครงสร้าง Storage เดิม
- เพิ่ม Take ใน Results ของ Destination Song
- ไม่เขียนทับ Source Take
- ไม่ลบ Takes เก่า
- ไม่สลับ Source Take ของ Repaint อัตโนมัติ
- ผู้ใช้สามารถกด Repaint บน Take ใหม่เพื่อทำงานต่อได้

### Metadata

บันทึกข้อมูลเท่าที่จำเป็น เช่น:

- Generation Mode: `normal` / `cover` / `repaint`
- Source Project ID
- Source Song ID
- Source Take ID (ถ้ามี)
- Source External Audio Reference (กรณี Cover External)
- Caption Snapshot
- Lyrics Snapshot สำหรับ Cover
- Repaint Start / End
- Workflow Identifier
- Generation Timestamp

ใช้ Schema ที่เข้ากับระบบเดิม หลีกเลี่ยงข้อมูลซ้ำซ้อน

### Failure Handling

หาก Queue ล้มเหลวหรือไม่มีไฟล์ Output ที่ถูกต้อง:

- ไม่สร้าง Take ที่ไม่สมบูรณ์
- ไม่ลบ Source Audio
- ไม่ทำให้ Results เสียหาย
- แสดง Error ที่เข้าใจได้
- เก็บ Draft ไว้เพื่อ Retry

---

## Phase 7 — Repaint Draft Persistence

บันทึก Draft แยกตาม:

`Project ID + Song ID + Take ID`

Draft ประกอบด้วย:

- Source Take
- Caption
- Start / End
- Waveform Zoom / View State เท่าที่จำเป็น

พฤติกรรม:

- เปลี่ยน Take แล้ว Draft ของ Take ก่อนหน้าต้องไม่หาย
- กลับมา Take เดิมต้องโหลด Draft เดิม
- Save Project แล้ว Restart ต้องกลับมาทำต่อได้
- ไม่บันทึก Audio Samples หรือ Waveform ขนาดใหญ่ลง Project JSON
- Cache Waveform แยกจาก Draft
- ไม่แก้ไข Song Lyrics หลัก

ตรวจ Compatibility กับ Project เก่าที่ไม่มีข้อมูล Cover/Repaint

---

## Phase 8 — UI Performance และ Thread Safety

ห้ามทำงานหนักใน Flet UI Thread

ต้องป้องกัน:

- Waveform Decode บล็อก UI
- สลับ Take เร็วแล้ว Waveform ผิดเพลง
- UI Update หลัง Control ถูก Dispose
- กด Generate/Repaint ซ้ำระหว่างส่ง Queue
- Queue Completion ผูกผิด Song/Take
- Project Switch ระหว่าง Generation
- Audio Player ใช้ Source เก่าหลังเปลี่ยน Take

ใช้ Background Task และ State Management ตามแนวทางที่มีอยู่ในโปรแกรม

หลีกเลี่ยงการ Rebuild Song Settings หรือ Editor ทั้งหน้าเมื่อแก้ Caption หรือเลื่อน Selection

---

## Phase 9 — Verification

หลัง Implement ให้ตรวจอย่างน้อย:

### Cover

- OFF → Normal Generate Path เดิม
- ON + External → Source ถูกต้อง
- ON + This Project → Song/Take ถูกต้อง
- Source กับ Destination ไม่สับสน
- Missing Source → แจ้ง Error
- Cover Result → Take ใหม่

### Repaint

- เปิดจาก Results แล้ว Source ถูกต้อง
- เปลี่ยน Song/Take แล้ว Waveform เปลี่ยนตาม
- Waveform มาจาก Audio จริง
- Drag Selection → Start/End ถูกต้อง
- Zoom แล้วเวลาไม่คลาดเคลื่อน
- Caption ส่งเข้า Node ถูกต้อง
- Repaint Result → Take ใหม่
- Source Take ไม่ถูกเปลี่ยน
- Draft จำแยกตาม Take

### Persistence

- Save/Load Project
- Restart
- Project เก่า
- Source File Missing
- Failed Queue
- Cancel/Retry
- เปลี่ยน Project ระหว่างรอผล

### Testing Policy

อนุญาต:

- Static Checks
- Import / Syntax Checks
- Unit Tests
- Mock ComfyUI API
- Mock Queue Completion
- UI Logic Tests
- Waveform Processing Tests ด้วยไฟล์เสียงทดสอบที่มีอยู่

**ไม่อนุญาตให้เรียกโมเดล ACE-Step เพื่อสร้างเสียงจริงโดยอัตโนมัติ**

ถ้าจำเป็นต้องทดสอบกับ ComfyUI จริง ให้รายงานขั้นตอน Manual Test สำหรับผู้ใช้แทน

---

## Deliverables

เมื่อทำเสร็จให้รายงาน:

1. ไฟล์ที่แก้ไขทั้งหมด
2. สิ่งที่ Implement สำเร็จ
3. Cover / Repaint Workflow ที่ใช้
4. Dependencies หรือ Models ที่ยังขาด
5. ผล Static และ Mock Tests
6. จุดที่ยังไม่ได้ Verify กับ ComfyUI จริง
7. ขั้นตอน Manual Test แบบสั้น
8. ข้อจำกัดที่ยังเหลือ

ห้ามรายงานว่าใช้งานได้จริงครบทุกส่วน หากยังไม่ได้ทดสอบ Runtime ที่เกี่ยวข้อง

## Acceptance Criteria

งานถือว่าเสร็จในส่วน Implementation เมื่อ:

- Cover UI ทำงานตามข้อกำหนด
- Repaint Tab ทำงานตามข้อกำหนด
- Waveform จริงแสดงและเลือกช่วงได้
- ส่ง Parameters ไปยัง Workflow ถูกต้อง
- Queue / Results Integration ครบ
- Take ใหม่ไม่เขียนทับ Take เดิม
- Draft และ Project Persistence ทำงาน
- Normal Generate เดิมไม่เปลี่ยนพฤติกรรม
- Tests ที่ไม่เรียกโมเดลผ่าน
- ไม่มี Regression ที่ตรวจพบในระบบเดิม

**ดำเนินการ Audit → Implement → Verify → Fix ให้จบต่อเนื่องในรอบเดียว โดยไม่ต้องถามยืนยันระหว่างขั้นตอน ยกเว้นพบ Blocker ที่ต้องติดตั้งระบบภายนอกหรือมีความเสี่ยงต่อข้อมูลเดิม**
