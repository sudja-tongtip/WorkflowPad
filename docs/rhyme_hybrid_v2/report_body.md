# Rhyme Analyzer Hybrid V2 — รายงาน Prototype (Python First → Gemma Review ทั้งเพลง → Python Merge)

Prototype แยกจาก Production: ไม่แก้โค้ด/Prompt Production, ไม่แตะ AppData จริง, ไม่ commit, ไม่รัน Analyze Song เต็มระบบ, ไม่เรียก Grok
Gemma ถูกเรียก **3 ครั้งรวม** (เพลงละ 1 request, ไม่ retry) ข้อมูลทั้งหมดอยู่ใน `docs/rhyme_hybrid_v2/`
เพลง: S1, S2, S3 และ Reference เดิมจาก `docs/analyze_song_audit/` เท่านั้น ไม่มี LLM Judge

## 0. คำตอบสุดท้าย

**Gemma Reviewer ไม่ได้เพิ่มความแม่นยำหรือความครอบคลุมจาก Python Only ในการทดสอบ 3 เพลงนี้ → ยังไม่คุ้มกับ request เพิ่มเพลงละ 1 ครั้ง (10–18 วินาที) ในรูปแบบ Prompt/โมเดลปัจจุบัน**
สิ่งที่ทำให้ผลดีขึ้นมากเทียบกับระบบเดิมคือ **Python First + Merge** ไม่ใช่ตัว Reviewer:

| | คู่ผิดที่ reference บอกว่าไม่สัมผัสแต่ระบบยืนยัน (FP) | คู่ถูกที่พบ (TP) | requests | เวลา LLM |
|---|---|---|---|---|
| ระบบเดิม (Gemma ทีละ Section → Songwriter ได้ผลดิบ), r1 / r2 | S1 3/5 และ 5/5 · S2 3/3 และ 2/3 · S3 1/1 และ 0/1 | S1 3/4 และ 4/4 | 6–8 ต่อเพลง | 25–45 วินาที |
| Python Only (prototype wrapper) | **0** ทุกเพลง | S1 **4/4** | 0 | 0 (Python ≤0.3 วินาที) |
| Hybrid V2 (Python + Gemma 1 request + Merge) | **0** ทุกเพลง | S1 **4/4** | 1 | 10.5–18 วินาที |

คู่ที่ Gemma เพิ่มและผ่านเป็น verified: **0 คู่** (ใน 3 เพลง); คู่ที่ Python พบถูกต้องไม่หาย; ไม่มีคำที่ไม่มีจริงหรือคู่ที่ถูกปฏิเสธหลุดเป็น verified
ข้อจำกัดของข้อสรุป: Python ได้ TP 4/4 และ FP 0 อยู่แล้ว จึงแทบไม่มี "ที่ว่าง" ให้ Reviewer ช่วย และ 3 เพลงนี้ไม่มีคำที่ Python อ่านไม่ได้ซึ่งเป็นจุดที่ Reviewer ควรมีประโยชน์ที่สุด (S1 L9 เป็นกรณีเดียว และ Gemma ตอบ `?`) → **ผลเรื่องความครอบคลุมสรุปไม่ได้หนักแน่น แต่ยังไม่มีหลักฐานว่าคุ้ม**

## 1. Architecture ของ Prototype

```
เนื้อเพลงต้นฉบับ
   │
   ▼  hybrid.python_engine()            (ใช้ live_insights.analyze + rhyme_map.python_links + thai_rhyme; 0.005–0.3 วินาที)
   │   - สัมผัสท้ายบรรทัด/สัมผัสใน, ตำแหน่งคำ + Line ID (offset ตัวอักษร)
   │   - คำซ้ำ (คำเดียวกัน) แยกเป็น repetition  ← wrapper ของ prototype (python_links เดิมยังนับเป็น exact)
   │   - ระยะ: same_section=high / adjacent_section=medium / distant=low  ← wrapper
   │   - รายการ "อ่านไม่ได้" (U) และ "ไม่มีคู่" ต่อบรรทัด
   ▼
   hybrid.build_review_request()         (เนื้อเพลงครบ + Line ID + ผล Python → ~580–900 tokens + system ~985)
   ▼
   Gemma Reviewer — 1 request ต่อเพลง    (gemma4_llm.json, ctx 19000, ตอบด้วย clear tags)
   ▼
   hybrid.parse_review()                 (ไวยากรณ์ [TYPE.n] เหมือน clear tags; tag แปลกถูกเก็บเป็น issue ไม่ล้มทั้งคำตอบ)
   ▼
   hybrid.merge()                        (ตรวจคำกับเนื้อเพลง, Line ID, ตำแหน่ง, เสียงด้วย Python (RM.relation), dedupe, repetition, ระยะ, เก็บ disagreement)
   ▼
   hybrid.to_evidence() → รูป dict เดียวกับ rhyme_flow.build_llm_evidence → Songwriter / UI
```

ถ้า Reviewer ล้มเหลว (error / ว่าง / อ่านไม่ได้) → `merge(eng, None, "python_only…")` ใช้ Python Only ได้ทันที (ทดสอบใน offline)

กติกา Merge (ย่อ):
- Python ตรวจเสียงผ่านเท่านั้นจึงเป็น `verified`; Confidence ของ Gemma ไม่เปลี่ยนสถานะใด ๆ
- Reviewer `wrong` หรือ `[DISPUTE]` ต่อคู่ที่ Python ยืนยัน → `disputed` (ไม่ลบ, ไม่ยกเป็น verified, เก็บเหตุผลทั้งสองฝั่ง)
- `[ADD]`: ตรวจว่าคำมีในบรรทัดจริง/ไม่ใช่ tag/ไม่ใช่เศษพยางค์/ตำแหน่งตรง → ให้ Python ตัดสินเสียง: ตรง → verified, ต่าง → rejected, คำเดิมซ้ำ → rejected (repetition), อ่านไม่ได้ → candidate
- คู่ซ้ำกับของ Python → รวม (ไม่เพิ่มซ้ำ); id ที่ไม่มีอยู่จริง → ไม่ใช้ + บันทึก
- Python ตรวจไม่ได้ ไม่ถือว่าผิดโดยอัตโนมัติ (→ candidate)

## 2. Prompt และ Schema ของ Reviewer

ข้อความเต็มอยู่ในภาคผนวก A (ไฟล์ `hybrid.py` → `REVIEW_SYSTEM`). Schema (clear tags, ไม่ใช้ JSON/XML):

| Tag | Field | หมายเหตุ |
|---|---|---|
| `[REVIEW.n]` | `pair: Pk`, `verdict: agree\|wrong\|unsure`, `reason` | หนึ่งต่อทุกคู่ของ Python |
| `[ADD.n]` | `relation`, `placement`, `confidence`, `reason` + 2 บรรทัด `L<n>\|คำ\|end\|inside` | คู่ที่ Python พลาด |
| `[DISPUTE.n]` | `target: Pk\|Rk\|Uk`, `python_says`, `you_say`, `reason` | ขัดแย้งเรื่องการอ่านเสียง/การจัดชนิด |
| `[SECTION.n]` | `name`, `checked: yes`, `note` | ยืนยันตรวจทุก Section |
| `[DONE]` | | |

ข้อสังเกตเรื่อง Contract: `clear_tags.REGISTRY` ปัจจุบันมี `REVIEW` อยู่แล้ว (ใช้ในรายงานอีกความหมาย) แต่ไม่มี `ADD` และ `DISPUTE` → ถ้านำเข้า Production ต้องเพิ่ม tag และระวังชื่อ `REVIEW` ชนกัน (Prototype ใช้ parser ของตัวเองด้วยไวยากรณ์เดียวกัน จึงไม่แตะ Production)

## 3. ผลเทียบ Python Only / Hybrid / ระบบเดิม (Reference เดิมเท่านั้น)

หน่วยนับ = คู่ของบรรทัด; Chorus ที่ซ้ำนับตามลำดับที่ปรากฏ (Chorus 1 กับ Chorus 2). "ไม่มีเฉลย" = Not Evaluated (ไม่นับถูกหรือผิด)

| เพลง | ระบบ | TP | FN | FP (ต่อ rhyme_false) | Not Evaluated |
|---|---|---|---|---|---|
| S1 (true 4 / false 5) | Python Only | 4 | 0 | 0 | 3 |
| | Hybrid verified | 4 | 0 | 0 | 2 |
| | Hybrid verified+disputed | 4 | 0 | 0 | 3 |
| | เดิม r1 (ส่งให้ Songwriter) | 3 | 1 | **3** | 10 |
| | เดิม r2 (ส่งให้ Songwriter) | 4 | 0 | **5** | 7 |
| S2 (true 0 / false 3) | Python Only | – | – | 0 | 1 |
| | Hybrid verified | – | – | 0 | 0 |
| | เดิม r1 / r2 | – | – | **3 / 2** | 9 / 5 |
| S3 (true 0 / false 1) | Python Only | – | – | 0 | 3 |
| | Hybrid verified | – | – | 0 | 3 |
| | เดิม r1 / r2 | – | – | **1 / 0** | 8 / 6 |

หมายเหตุ: "ระบบเดิม" ในตารางคือคู่ที่ LLM เสนอ (`res.llm`) ซึ่งเป็นสิ่งที่ Songwriter ได้รับจริงตามที่ตรวจในรอบก่อน; คู่ที่เหลือหลังตรวจเสียงด้วย Python ในระบบเดิม (`res.pairs`) ได้ FP 0 แต่ TP 0/4 (Python ปฏิเสธแทบทุกคู่ของ Gemma) — ข้อมูลอยู่ใน `results/score.json`
Python Only = `python_links` + wrapper ของ prototype (แยกคำซ้ำ + ธงระยะ) เท่านั้น ผลที่ Reference วัด (TP/FP) **ไม่ต่างกัน** ระหว่างมี/ไม่มี wrapper; wrapper ต่างที่ Not Evaluated (เช่น ไป/ไป ใน S3 ไม่ถูกนับเป็น rhyme) และที่ Songwriter evidence

คู่ที่ Python พบแต่ Reference ไม่มีเฉลย (Not Evaluated) และสถานะใน Hybrid:
- S1: L3 นาน↔L20 ช่าง (near, distant → **disputed** เพราะ Gemma โต้แย้ง), L15 ใส↔L18 ไร, L18 ไร↔L26 ไห้ (exact, adjacent_section → verified, medium)
- S2: L5 ไร↔L13 ไหม (exact, distant → **disputed**, low) — ทำให้ไม่ถูกนับเป็นสัมผัสที่ยืนยัน
- S3: L3 ควัน↔L5 นั้น (verified), L4 ยืม↔L17 คืน และ L5 นั้น↔L16 ร้าน (near, distant → listed "VERIFIED BUT DISTANT"), ยืน↔กลืน (internal verified); ไป↔ไป ×3 → repetition

## 4. ตัวชี้วัดตาม Brief

| Metric | ผล |
|---|---|
| True Positive | ไม่สูญเสียคู่ที่ Python พบถูก: S1 4/4 ทั้ง Python Only และ Hybrid |
| False Positive | ไม่เพิ่ม: Hybrid 0 ทั้ง 3 เพลง (ระบบเดิม 1–5 คู่) |
| False Negative (Reviewer ช่วยพบ?) | **ไม่** — ADD ที่ผ่านเป็น verified = 0 คู่; S1 L9 ที่ Python อ่านไม่ได้ Gemma ตอบ `?` |
| Section Coverage | S1 5/5 ชื่อ Section, S2 3/3, S3 3/3 (นับตามชื่อ — Chorus ซ้ำนับครั้งเดียว) ผ่านเมื่อ Gemma ยืนยันครบ |
| Hallucination | ADD 2 รายการใน S2 ใช้ `ตาม` เป็นคำท้ายบรรทัด (ตามปกติ) → ถูกบล็อก "word is not at the end of the line"; id คู่ที่ไม่มีจริง P2–P4 ใน S2 → ถูกบล็อก 6 รายการ; ไม่มีรายการใดหลุดเข้า verified |
| Requests | 1 ต่อเพลง (รวม 3) ✓ |
| Runtime | Python 0.29 / 0.005 / 0.007 วินาที · Gemma 18.0 / 11.0 / 10.5 วินาที · Merge <0.01 วินาที (เดิม: 6–8 requests, 25–45 วินาที ต่อเพลง) |
| Evidence | คู่ rejected ที่ถูกจัดเป็น "pair" ใน evidence = 0 ในทั้ง 3 เพลง |

## 5. ปัญหาที่พบและ Root Cause

1. **Gemma ไม่ตามรูปแบบ `[ADD]`** — S1 ADD 9/9 และ S3 6/6 ไม่มีบรรทัด endpoint `L<n>|คำ|role` (เขียนเหตุผลเป็นข้อความแทน) → Merge บล็อก 15 รายการ ("0 endpoints"); ใน S2 ทำตามรูปแบบครบ — Root cause: ความสม่ำเสมอของโมเดลต่อ schema ที่ endpoint เป็นบรรทัดเปล่าต่อท้าย field (ไม่ใช่ข้อบกพร่องของ Merge). ยิงซ้ำไม่ได้ (จำกัด 3 requests)
2. **Gemma เห็นด้วยกับ Python ทุกคู่ (sycophancy)** — S1 8/9 agree (1 unsure), S3 4/4 agree แม้คู่ยืม↔คืนข้ามไกล; ใน S2 ยัง "agree" กับคู่ P2–P4 ที่ไม่มีอยู่จริง (ตัวเองแต่งขึ้นจากบรรทัดซ้ำ ตัวเอง/ตัวเอง และบอกว่า "Python listed it") → Merge บล็อกได้ แต่แสดงว่า REVIEW ไม่ใช่สัญญาณคุณภาพที่ไว้ใจได้
3. **ละเมิดกฎ "คำซ้ำไม่ใช่สัมผัส" ที่ใส่ใน Prompt** — S3 ADD ทั้ง 6 รายการเป็นคำเดียวกันซ้ำ (เธอ/เธอ, เปลี่ยนไป/เปลี่ยนไป) ประกาศเป็น "rhymes perfectly"
4. **เหตุผลแบบหาข้ออ้าง** — S1/S2 ADD อ้าง "ถ้าออกเสียงให้ใกล้ขึ้น" ให้เสียงต่างกันเป็น exact (เช่น เบา↔เธอ, วัน↔ตาม); เมื่อจำลองดึงคำจากเหตุผลให้ (`whatif_extract_endpoints.py`, ไม่ใช่ request ใหม่) Python ปฏิเสธ 4 จาก 5 คู่ใหม่ใน S1 (อีก 4 รายการซ้ำคู่ของ Python เอง; คู่ที่ผ่านคือ ไห้↔ใส ข้าม Chorus ซึ่งเสียงตรงจริงแต่เป็นการจับคู่ซ้ำของท่อนเดิม) และปฏิเสธ 4/4 ใน S2
5. **`[DISPUTE]` กำกวม** — S1 Gemma ว่า นาน↔ช่าง "ใกล้กว่า near" (exact) → Merge จึงลดคู่ที่ Python ยืนยัน (near) เป็น `disputed` ทั้งที่ Gemma ไม่ได้บอกว่าไม่สัมผัส. ควรมี field `kind: relation|sound|repetition` เพื่อให้นโยบายต่างกัน (ต้องแก้ Prompt = ยิงใหม่)
6. **ข้อจำกัดฝั่ง Python ที่ยังเหลือ** (ไม่มี Reviewer ช่วย): S1 L9 `แค่ยืนใกล้ก็รู้ทั้งหมด` อ่านเสียงท้ายบรรทัดไม่ได้; คำหลายพยางค์ที่ตัดไม่ได้; สัมผัสในคำประสม; อังกฤษเป็นการประมาณ (รายละเอียดใน `PYTHON_RHYME_ENGINE_AUDIT.md`)
7. **`python_links` เดิมยังนับ ไป/ไป เป็น exact** (ไม่ได้ใช้การตรวจ repetition ของ `relation()`) — prototype แก้ที่ wrapper เท่านั้น; ถ้านำเข้า Production ควรแก้ที่ต้นทาง

## 6. ความพร้อมในการเชื่อม Songwriter / UI

- รูป evidence ของ prototype เข้ากับ `rhyme_flow.build_llm_evidence` (sections → lines/pairs/repetitions/not_confirmed/rejected); `RF.evidence_text` อ่านได้ไม่ error (ทดสอบ) **แต่** เปลี่ยน Contract อย่างน้อยดังนี้ (ยังไม่แก้ Production ตามข้อห้าม — รายงานก่อน):
  1. ข้อความหัวของ `evidence_text` บอกว่าเป็น "การอ่านเสียงของ Rhyme Analyzer AI … ห้ามขัดแย้ง" ซึ่งไม่ถูกกับ Hybrid และ **ไม่พิมพ์ `not_confirmed`/`rejected`** → candidate/disputed จะหายไปจากสายตา Songwriter. Prototype จึงมี `evidence_text_v2` (VERIFIED / CANDIDATE / DISPUTED / REPETITION แยกกัน, rejected นับเฉย ๆ ไม่แสดงเป็นคู่)
  2. ค่า `verification` ใน evidence เดิมคือ "analyzer" — ต้องรองรับ python / candidate / disputed
  3. `clear_tags.REGISTRY` ต้องเพิ่ม `ADD`, `DISPUTE` (และแก้ความกำกวมของ `REVIEW`)
  4. เส้นทางสี/UI (`RC.colorize` → Rhyme Map `Link`) ใช้ Python links อยู่แล้ว; ผล Hybrid ต้องแปลง verified → Link(source=python+llm) และ candidate/disputed → ไม่ระบายสี
  5. Songwriter ที่ใช้อยู่ดึง evidence จาก `res.llm` (music_studio.py:6971, 8935) → ต้องเปลี่ยนจุดนั้นเป็นผล Merge
- Line ID/Section คงเดิมทุกคู่ (`file_line` + offset); Producer ไม่รับ rhyme evidence (ตามที่ระบุ ไม่ได้ทำอะไร)

## 7. Regression / Safety (offline, ไม่มี LLM)

26/26 ผ่าน (ตารางที่ภาคผนวก D): คำตอบผิดรูป/ว่าง/JSON/tag แปลก/ไม่มี [DONE], คำที่ไม่มีในเพลง, tag line, เศษพยางค์, คู่ซ้ำ, id ที่ไม่มี, คำซ้ำ, คู่ข้าม Section ไกล, เพลงไม่มีสัมผัสที่ Reviewer อ้างสัมผัสทุกที่ (ไม่มี pair ของ LLM ผ่านเป็น verified), Reviewer ล้มเหลว → Python Only, ไม่มี field edit/apply ใน output และเนื้อเพลงไม่เปลี่ยน
ยังไม่ได้ทดสอบ: Timeout จริงของ ComfyUI (จำลองด้วยการ merge ด้วย `None`), เพลงยาวเกิน Context Budget (prototype ยังไม่ได้ทำการตรวจงบ Context — ต้องเพิ่ม: ถ้า prompt ประเมินเกิน ctx ให้ข้าม Reviewer ใช้ Python Only)

## 8. Acceptance Criteria

| เกณฑ์ | ผล |
|---|---|
| Gemma ตรวจทั้งเพลงใน 1 Request | ผ่าน (3/3) |
| คู่ผิดที่ทราบแน่ไม่กลายเป็น Verified | ผ่าน (FP 0) |
| คู่ที่ Python ถูกใน Reference ไม่สูญหาย | ผ่าน (4/4) |
| Reviewer เสนอคู่เพิ่มพร้อมหลักฐาน | **ไม่ผ่านจริง** — เสนอแต่ใช้ไม่ได้ (รูปแบบผิด/เสียงไม่ตรง/คำซ้ำ) 0 คู่ใหม่ที่ถูกต้อง |
| Songwriter evidence ไม่ปนผลดิบที่ถูก Rejected | ผ่าน |
| Reviewer Failure → Python Only | ผ่าน |
| ไม่แก้ Production | ผ่าน (ไฟล์ที่สร้างอยู่ใน docs/rhyme_hybrid_v2/ ทั้งหมด; ไม่มีไฟล์ใน music_studio/ ถูกแก้ระหว่างงานนี้) |

## 9. คำแนะนำ

1. **ใช้ Python First + Merge แทน Rhyme Analyzer เดิมได้** (ระบบเดิมใช้ 6–8 requests ต่อเพลงแล้วส่งคู่ผิดให้ Songwriter; Python ให้ TP/FP ที่ดีกว่าในทุกเพลงที่ทดสอบและเร็วกว่าเป็นร้อยเท่า) — ต้องแก้ Production 3 จุดก่อน: (ก) ต้นทาง repetition ใน `python_links`, (ข) Songwriter evidence ต้องมาจากผล Merge, (ค) ข้อความ evidence ใหม่
2. **ยังไม่ควรเปิด Gemma Reviewer เป็นค่าเริ่มต้น** — 3 เพลง: ประโยชน์ 0 คู่, ต้นทุน 10–18 วินาที/เพลง, ความเสี่ยงคือ DISPUTE ลดระดับคู่ที่ถูก (S1) ถ้าจะเก็บไว้ให้เป็นตัวเลือกที่ปิดอยู่และให้ผลลัพธ์เป็น `candidate` เท่านั้น
3. ถ้าอยากประเมิน Reviewer ต่อ: ปรับ Prompt (endpoint เป็น field `a:`/`b:`, เพิ่ม `kind:` ใน DISPUTE, ห้ามอ้าง pair id ที่ไม่มีในรายการ) แล้วทดสอบกับเพลงที่ Python อ่านไม่ได้จริง (คำหลายพยางค์ที่ตัดไม่ได้, สัมผัสในคำประสม, อังกฤษ) — 3 เพลงนี้ไม่มีกรณีพอ
4. เพิ่มตรวจ Context Budget ก่อนเรียก Reviewer และข้ามไป Python Only เมื่อเกิน

ข้อจำกัดของรายงาน: 3 เพลง; Reference เล็ก (S1 true 4 / false 5, S2 false 3, S3 false 1) ส่วนใหญ่ Not Evaluated; Gemma 1 ครั้งต่อเพลง (ผลอาจต่างเมื่อสุ่มใหม่); ไม่ได้ฟังเสียงร้อง
