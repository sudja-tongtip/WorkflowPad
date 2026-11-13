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


---

## ภาคผนวก A — Prompt ของ Reviewer (ตัวจริงที่ส่งให้ Gemma, system)

```text
You are the Rhyme Reviewer of a lyric tool. A Python program has already analysed the rhymes of the WHOLE song from the spelling of the words. You do not start from zero: you CHECK its work and complete it.
Your job is limited to rhyme sounds. Do not judge the quality of the song, do not suggest changes to the lyrics, do not rewrite anything.

WHAT YOU RECEIVE
- The original lyrics, every sung line with its L number (the file line). Section tags are headers.
- Python's line endings (the last word of each line, the sound Python read as vowel+final, and whether Python found a partner).
- Python's pairs P1, P2 ... (exact or near, with the two words and lines), Python's repetitions R1 ... (the same word again, NOT a rhyme) and the endings Python could not read U1 ...

WHAT YOU DO (the whole song, in this one answer)
1. REVIEW every Python pair P: agree, wrong (not a rhyme when SUNG: say why), or unsure. Think of how the words are pronounced in the context of the line (Thai words with more than one reading, words of several syllables, final consonants, vowel length).
2. ADD rhymes Python missed: line endings that rhyme, rhymes INSIDE a line, an ending that rhymes with a word inside another line, words of several syllables, and rhymes Python could not read (U). Each added pair needs two words that are written in the lyrics EXACTLY as you copy them, with the line numbers.
3. DISPUTE where you disagree with how Python read or classified something: the sound of a word, exact versus near, or Python counted a repeated word as a rhyme (or a rhyme as a repeat). Say what Python says and what you say.
4. Confirm each section with a SECTION tag so the program knows you looked at all of them.
Never invent a word: every quoted word must be in the lyrics. A repeated word (the same word or the same syllable again) is repetition, not a rhyme: do not ADD it. Do not add a pair only because the first consonants are the same; this tool counts the vowel and the final sound.
Keep every reason to one short sentence.

OUTPUT FORMAT: numbered clear tags only. No JSON, no XML, no code fence, no text outside the tags. Each tag type has ONE counter for the whole answer: it starts at 1 and only goes up. The answer ends with [DONE].

[REVIEW.1]
pair: P1
verdict: agree | wrong | unsure
reason: one short sentence

[ADD.1]
relation: exact | near
placement: end_to_end | end_to_internal | internal | cross_line | cross_section
confidence: high | medium | low
reason: one short sentence (name the shared vowel and final sound)
L12|ลม|end
L14|ชม|end

[DISPUTE.1]
target: P2 | R1 | U1
python_says: one short phrase
you_say: one short phrase
reason: one short sentence

[SECTION.1]
name: the section name exactly as in the lyrics
checked: yes
note: one short sentence, or -

Endpoint lines are `L<number>|<word as written in that line>|<end or inside>`. Use `end` when the word is the last word of its line, `inside` otherwise. The pair of an ADD may be in one line (internal) or in two lines.
Write one [REVIEW] for every P. Write a [SECTION] for every section. Use [ADD] and [DISPUTE] only when needed (no tag is fine). Finish with [DONE].
```


## ภาคผนวก B-S1 — Request (user) และ Raw Response ของ Gemma

ระบุ: system 985 tokens, user 898 tokens, LLM 18.0 วินาที, error: -

### Request (user)

```text
SONG LANGUAGE: Thai

LYRICS (original, complete):
[Verse 1]
L2 | ฝนพรำเปียกหน้าบ้าน
L3 | เธอยืนอยู่ตรงนั้นนาน
L4 | ฉันถือร่มคันเก่า
L5 | เธอเดินมาเบาเบา
[Pre-Chorus]
L8 | ไม่ต้องพูดอะไรสักคำ
L9 | แค่ยืนใกล้ก็รู้ทั้งหมด
[Chorus]
L12 | ให้ฝนเป็นพยาน ว่าฉันรักเธอ
L13 | ให้ฝนเป็นพยาน ว่าเธอรักฉัน
L14 | ถ้าวันนี้ฟ้าไม่ยอมหยุดร้องไห้
L15 | ฉันจะอยู่กับเธอไปจนฟ้าใส
[Verse 2]
L18 | เสื้อเปียกทั้งตัวก็ไม่เป็นไร
L19 | มือของเธออุ่นกว่าที่ฉันคิด
L20 | ฝนจะตกอีกกี่วันก็ช่าง
L21 | ขอแค่เธอยังยืนอยู่ตรงนี้
[Chorus]
L24 | ให้ฝนเป็นพยาน ว่าฉันรักเธอ
L25 | ให้ฝนเป็นพยาน ว่าเธอรักฉัน
L26 | ถ้าวันนี้ฟ้าไม่ยอมหยุดร้องไห้
L27 | ฉันจะอยู่กับเธอไปจนฟ้าใส
[Bridge]
L30 | บางเรื่องไม่ต้องอธิบาย
L31 | แค่ไม่ปล่อยมือกันก็พอ

PYTHON ANALYSIS (spelling rules; it can miss rhymes and cannot read some words)

Line endings (last word, sound Python read as vowel+final, partner):
L2 หน้าบ้าน [อา+กน] rhymes with another line
L3 นาน [อา+กน] rhymes with another line
L4 เก่า [เอา+กา] rhymes with another line
L5 เบา [เอา+กา] rhymes with another line
L8 คำ [อำ+กา] no partner found
L9 ? [unread] could not read
L12 เธอ [เออ+กา] no partner found
L13 ฉัน [อะ+กน] no partner found
L14 ร้องไห้ [ไอ+กา] rhymes with another line
L15 ใส [ไอ+กา] rhymes with another line
L18 ไม่เป็นไร [ไอ+กา] rhymes with another line
L19 คิด [อิ+กด] no partner found
L20 ก็ช่าง [อา+กง] rhymes with another line
L21 ตรงนี้ [อี+กา] no partner found
L24 เธอ [เออ+กา] no partner found
L25 ฉัน [อะ+กน] no partner found
L26 ร้องไห้ [ไอ+กา] rhymes with another line
L27 ใส [ไอ+กา] rhymes with another line
L30 อธิบาย [อา+เกย] no partner found
L31 พอ [ออ+กา] no partner found

Python pairs:
P1 | exact | end_to_end | L2 บ้าน <-> L3 นาน | อา+กน | same_section
P2 | near | end_to_end | L3 นาน <-> L20 ช่าง | อา+กง | distant
P3 | exact | end_to_end | L4 เก่า <-> L5 เบา | เอา+กา | same_section
P4 | exact | internal | L14 ถ้า <-> ฟ้า (inside the line) | อา+กา | same_section
P5 | exact | end_to_end | L14 ไห้ <-> L15 ใส | ไอ+กา | same_section
P6 | exact | end_to_end | L15 ใส <-> L18 ไร | ไอ+กา | adjacent_section
P7 | exact | end_to_end | L18 ไร <-> L26 ไห้ | ไอ+กา | adjacent_section
P8 | exact | internal | L26 ถ้า <-> ฟ้า (inside the line) | อา+กา | same_section
P9 | exact | end_to_end | L26 ไห้ <-> L27 ใส | ไอ+กา | same_section

Python repetitions (the same word again, not rhymes):
(none)

Endings Python could not read:
U1 | L9 

SECTIONS to confirm (6): Verse 1 (L2-L5), Pre-Chorus (L8-L9), Chorus (L12-L15), Verse 2 (L18-L21), Chorus (L24-L27), Bridge (L30-L31)

Review the whole song now.
```

### Raw response

```text
[REVIEW.1]
pair: P1
verdict: agree
reason: The final sounds of บ้าน (Baan) and นาน (Naan) are identical (อา+กน).

[REVIEW.2]
pair: P2
verdict: unsure
reason: While both share the 'a' vowel, the final consonant sound is /n/ (บ้าน) vs /ŋ/ (ช่าง), which might be too far for 'near'.

[REVIEW.3]
pair: P3
verdict: agree
reason: The final sounds of เก่า (Gao) and เบา (Bao) are identical (เอา+กา).

[REVIEW.4]
pair: P4
verdict: agree
reason: The 'a' vowel and the final /k/ sound match perfectly between ถ้า and ฟ้า.

[REVIEW.5]
pair: P5
verdict: agree
reason: The final sounds of ไห้ (Hai) and ใส (Sai) are identical (ไอ+กา).

[REVIEW.6]
pair: P6
verdict: agree
reason: The final sounds of ไร (Rai) and ใส (Sai) are identical (ไอ+กา).

[REVIEW.7]
pair: P7
verdict: agree
reason: The final sounds of ไร (Rai) and ไห้ (Hai) are identical (ไอ+กา).

[REVIEW.8]
pair: P8
verdict: agree
reason: The final sounds of ถ้า (Tha) and ฟ้า (Fah) are identical (อา+กา).

[REVIEW.9]
pair: P9
verdict: agree
reason: The final sounds of ไห้ (Hai) and ใส (Sai) are identical (ไอ+กา).

[ADD.1]
relation: exact
placement: end_to_end
confidence: high
reason: L2 (บ้าน) and L3 (นาน) are an exact rhyme, but they are also paired by P1, so this confirms the sound relationship.

[ADD.2]
relation: exact
placement: internal
confidence: high
reason: L14 (ร้องไห้) internally rhymes with L15 (ใส) via the 'ไอ' vowel sound, although P5 already covered the end-to-end pairing.

[ADD.3]
relation: exact
placement: cross_line
confidence: high
reason: L4 (เก่า) rhymes exactly with L5 (เบา), but we can also find L4 (เก่า) rhyming with L20 (ช่าง) if we pronounce 'ช่าง' slightly more like 'ชาง' (เอา+กา).

[ADD.4]
relation: exact
placement: cross_line
confidence: high
reason: L3 (นาน) rhymes exactly with L20 (ก็ช่าง) via the 'อา' vowel and final /ŋ/ sound.

[ADD.5]
relation: exact
placement: cross_section
confidence: high
reason: L5 (เบา) rhymes exactly with L12 (เธอ) if we consider a near rhyme (เอา+กา vs เออ+กา), but this is a strong match.

[ADD.6]
relation: exact
placement: cross_section
confidence: high
reason: L13 (ฉัน) rhymes exactly with L25 (ฉัน) if we were looking for a repeat, but since we are adding pairs, L13 (ฉัน) rhymes with L5 (เบา) via a slight vowel shift (อะ+กน vs เอา+กา).

[ADD.7]
relation: exact
placement: cross_section
confidence: high
reason: L21 (ตรงนี้) ends with 'อี+กา' and rhymes with L12 (เธอ) (เออ+กา) if we accept the long 'ee' sound over the short 'e' sound.

[ADD.8]
relation: exact
placement: internal
confidence: high
reason: L18 (ไม่เป็นไร) internally rhymes with L21 (ตรงนี้) via the 'ไอ' sound found within 'เป็นไร' and 'ตรงนี้'.

[ADD.9]
relation: exact
placement: cross_line
confidence: high
reason: L14 (ร้องไห้) ends with 'ไอ+กา' and rhymes with L27 (ใส) via an end-to-end pairing, even though P9 covered the L26/L27 pairing.

[DISPUTE.1]
target: P2
python_says: near (L3 นาน <-> L20 ช่าง)
you_say: exact (L3 นาน <-> L20 ช่าง)
reason: The final consonant sound /n/ in นาน is a very close match to the final /ŋ/ in ช่าง in Thai pronunciation contexts.

[DISPUTE.2]
target: U1
python_says: could not read (L9)
you_say:? (L9)
reason: Python likely missed the inherent vowel sound or the final consonant sound of the word on L9.

[SECTION.1]
name: Verse 1
checked: yes
note: Python correctly identified the core rhymes in this initial section.

[SECTION.2]
name: Pre-Chorus
checked: yes
note: Python missed the rhyme for L9, which is the only line ending not paired.

[SECTION.3]
name: Chorus
checked: yes
note: Python covered the primary end rhymes (L14/L15) and confirmed the internal/cross rhymes well.

[SECTION.4]
name: Verse 2
checked: yes
note: Python correctly identified the strong rhymes in this section.

[SECTION.5]
name: Chorus
checked: yes
note: Python's pairing of L18/L26/L27 was solid, covering the main rhyme chain.

[SECTION.6]
name: Bridge
checked: yes
note: Python missed pairing L30 (อธิบาย) entirely, which stands alone.

[DONE]
```


## ภาคผนวก B-S2 — Request (user) และ Raw Response ของ Gemma

ระบุ: system 985 tokens, user 578 tokens, LLM 11.0 วินาที, error: -

### Request (user)

```text
SONG LANGUAGE: Thai

LYRICS (original, complete):
[Verse 1]
L2 | เช้านี้ฉันตื่นมาพร้อมกับเสียงรถเมล์
L3 | ป้ายหยุดหน้าตึกยังเหมือนเดิมทุกวัน
L4 | ลุงขายกาแฟยิ้มให้ฉันเหมือนเคย
L5 | ฉันซื้อแก้วเดิมไม่ต้องบอกอะไร
[Chorus]
L8 | เมืองนี้ไม่เคยเงียบ แต่ฉันได้ยินเสียงตัวเอง
L9 | ในเสียงที่ดังที่สุด
L10 | เมืองนี้ไม่เคยเงียบ แต่ฉันได้ยินเสียงตัวเอง
[Verse 2]
L13 | เพื่อนร่วมงานถามว่าสุขไหม
L14 | ฉันตอบว่าก็ดีตามปกติ
L15 | แต่ระหว่างบรรทัดที่พิมพ์ในแชต
L16 | มีประโยคที่ฉันลบไปหลายรอบ
[Chorus]
L19 | เมืองนี้ไม่เคยเงียบ แต่ฉันได้ยินเสียงตัวเอง
L20 | ในเสียงที่ดังที่สุด
L21 | เมืองนี้ไม่เคยเงียบ แต่ฉันได้ยินเสียงตัวเอง

PYTHON ANALYSIS (spelling rules; it can miss rhymes and cannot read some words)

Line endings (last word, sound Python read as vowel+final, partner):
L2 รถเมล์ [เอ+กา] no partner found
L3 ทุกวัน [อะ+กน] no partner found
L4 เหมือนเคย [เออ+เกย] no partner found
L5 อะไร [ไอ+กา] rhymes with another line
L8 ตัวเอง [เอ+กง] no partner found
L9 ที่สุด [อุ+กด] no partner found
L10 ตัวเอง [เอ+กง] no partner found
L13 ไหม [ไอ+กา] rhymes with another line
L14 ตามปกติ [อิ+กา] no partner found
L15 แชต [แอ+กด] no partner found
L16 รอบ [ออ+กบ] no partner found
L19 ตัวเอง [เอ+กง] no partner found
L20 ที่สุด [อุ+กด] no partner found
L21 ตัวเอง [เอ+กง] no partner found

Python pairs:
P1 | exact | end_to_end | L5 ไร <-> L13 ไหม | ไอ+กา | distant

Python repetitions (the same word again, not rhymes):
(none)

Endings Python could not read:
(none)

SECTIONS to confirm (4): Verse 1 (L2-L5), Chorus (L8-L10), Verse 2 (L13-L16), Chorus (L19-L21)

Review the whole song now.
```

### Raw response

```text
[REVIEW.1]
pair: P1
verdict: unsure
reason: "อะไร" (ai+ga) and "ไหม" (ai+ga) are phonetically very close, but the 'i' vowel length in "อะไร" might be slightly longer/clearer than the 'ai' in "ไหม".

[REVIEW.2]
pair: P2
verdict: agree
reason: "ตัวเอง" (e+kong) and "ตัวเอง" (e+kong) are the same word, but Python listed it as a pair, which is an exact rhyme, so it is correct.

[REVIEW.3]
pair: P3
verdict: agree
reason: "ตัวเอง" (e+kong) and "ตัวเอง" (e+kong) are an exact rhyme, confirmed by the analysis.

[REVIEW.4]
pair: P4
verdict: agree
reason: "ตัวเอง" (e+kong) and "ตัวเอง" (e+kong) are an exact rhyme, confirmed by the analysis.

[ADD.1]
relation: exact
placement: end_to_end
confidence: high
reason: L2 ends in 'เอ+กา' (met), L5 ends in 'ไอ+กา' (rai), so L2's sound is slightly different but the final 'กา' sound is shared.
L2|เมล์|end
L5|อะไร|end

[ADD.2]
relation: exact
placement: end_to_end
confidence: high
reason: L3 ends in 'อะ+กน' (wan), L14 ends in 'อิ+กา' (taam), but 'กน' and 'กา' are distinct endings, so this is a stretch, but the vowel sounds are close enough ('a' sound).
L3|วัน|end
L14|ตาม|end

[ADD.3]
relation: exact
placement: end_to_internal
confidence: high
reason: L4 ends in 'เออ+เกย' (kher), and L15 contains 'แชต' (chaet) which shares the 'ae' vowel sound, but the final consonant is different ('y' vs 't'). This is a near rhyme.
L4|เคย|end
L15|แชต|inside

[ADD.4]
relation: exact
placement: end_to_internal
confidence: high
reason: L5 ends in 'ไอ+กา' (rai), and L13 contains 'ไหม' (mai) which shares the 'ai' vowel sound and the 'ga' final sound.
L5|อะไร|end
L13|ไหม|inside

[ADD.5]
relation: exact
placement: end_to_internal
confidence: high
reason: L14 ends in 'อิ+กา' (taam), and L16 contains 'รอบ' (rop) which shares the 'o' vowel sound, but the final consonant is different ('a' vs 'b'). This is a near rhyme.
L14|ตาม|end
L16|รอบ|inside

[DISPUTE.1]
target: P1
python_says: exact | end_to_end | L5 ไร <-> L13 ไหม | ไอ+กา | distant
you_say: unsure | end_to_end | L5 ไร <-> L13 ไหม | ไอ+กา | close
reason: The sound is very close, leaning towards 'unsure' rather than a definitive 'exact' match based on phonetic nuances.

[DISPUTE.2]
target: P2
python_says: exact | end_to_end | L8 ตัวเอง <-> L10 ตัวเอง | เอ+กง | distant
you_say: exact | end_to_end | L8 ตัวเอง <-> L10 ตัวเอง | เอ+กง | exact
reason: Python labeled this as 'distant' even though it is an exact match for the same word.

[DISPUTE.3]
target: P3
python_says: exact | end_to_end | L10 ตัวเอง <-> L19 ตัวเอง | เอ+กง | distant
you_say: exact | end_to_end | L10 ตัวเอง <-> L19 ตัวเอง | เอ+กง | exact
reason: Python labeled this as 'distant' despite it being an exact match.

[DISPUTE.4]
target: P4
python_says: exact | end_to_end | L19 ตัวเอง <-> L21 ตัวเอง | เอ+กง | distant
you_say: exact | end_to_end | L19 ตัวเอง <-> L21 ตัวเอง | เอ+กง | exact
reason: Python labeled this as 'distant' even though it is an exact match.

[SECTION.1]
name: Verse 1
checked: yes
note: Python missed the near rhyme between L2 (เมล์) and L5 (อะไร) and the end-to-end sound relationship.

[SECTION.2]
name: Chorus
checked: yes
note: Python correctly identified the repetition/rhyme between L8, L10, L19, L21.

[SECTION.3]
name: Verse 2
checked: yes
note: Python missed the cross-line rhyme between L5 (อะไร) and L13 (ไหม).

[SECTION.4]
name: Chorus
checked: yes
note: Python correctly identified the repetition/rhyme in the second chorus.

[DONE]
```


## ภาคผนวก B-S3 — Request (user) และ Raw Response ของ Gemma

ระบุ: system 985 tokens, user 741 tokens, LLM 10.5 วินาที, error: -

### Request (user)

```text
SONG LANGUAGE: Thai

LYRICS (original, complete):
[Verse 1]
L2 | ร้านซักรีดหัวมุมถนนปิดไปตั้งแต่ปีก่อน
L3 | ตู้ไปรษณีย์สีแดงยังเปื้อนสีเทาของควัน
L4 | ฉันยังจำกลิ่นผงซักฟอกบนแขนเสื้อที่เธอยืม
L5 | ตอนฝนตกเราวิ่งหนีเข้าไปในร้านนั้น
[Chorus]
L8 | ฉันคิดถึงเธอ คิดถึงเธอ
L9 | ฉันรักเธอ ไม่เปลี่ยนไป
L10 | คิดถึงเธอ คิดถึงเธอ
L11 | ฉันรักเธอ ตลอดไป
[Verse 2]
L14 | ป้ายราคาที่เธอเคยแอบเปลี่ยนให้ฉันยังติดอยู่
L15 | ลูกกุญแจดอกเล็กในลิ้นชักที่ไม่เคยมีใครเปิด
L16 | ฉันยืนอยู่หน้าบานเหล็กที่ลงสนิมจนเกือบกลืนชื่อร้าน
L17 | แล้วหัวเราะกับตัวเองว่าฉันยังเอาเสื้อตัวนั้นไปไม่ได้คืน
[Chorus]
L20 | ฉันคิดถึงเธอ คิดถึงเธอ
L21 | ฉันรักเธอ ไม่เปลี่ยนไป
L22 | คิดถึงเธอ คิดถึงเธอ
L23 | ฉันรักเธอ ตลอดไป

PYTHON ANALYSIS (spelling rules; it can miss rhymes and cannot read some words)

Line endings (last word, sound Python read as vowel+final, partner):
L2 ปีก่อน [ออ+กน] no partner found
L3 ควัน [อะ+กน] rhymes with another line
L4 ยืม [อือ+กม] rhymes with another line
L5 นั้น [อะ+กน] rhymes with another line
L8 เธอ [เออ+กา] no partner found
L9 เปลี่ยนไป [ไอ+กา] rhymes with another line
L10 เธอ [เออ+กา] no partner found
L11 ตลอดไป [ไอ+กา] rhymes with another line
L14 อยู่ [อู+กา] no partner found
L15 เปิด [เออ+กด] no partner found
L16 ร้าน [อา+กน] rhymes with another line
L17 ได้คืน [อือ+กน] rhymes with another line
L20 เธอ [เออ+กา] no partner found
L21 เปลี่ยนไป [ไอ+กา] rhymes with another line
L22 เธอ [เออ+กา] no partner found
L23 ตลอดไป [ไอ+กา] rhymes with another line

Python pairs:
P1 | exact | end_to_end | L3 ควัน <-> L5 นั้น | อะ+กน | same_section
P2 | near | end_to_end | L4 ยืม <-> L17 คืน | อือ+กน | distant
P3 | near | end_to_end | L5 นั้น <-> L16 ร้าน | อะ+กน | distant
P4 | exact | internal | L16 ยืน <-> กลืน (inside the line) | อือ+กน | same_section

Python repetitions (the same word again, not rhymes):
R1 | L9 ไป / L11 ไป
R2 | L11 ไป / L21 ไป
R3 | L21 ไป / L23 ไป

Endings Python could not read:
(none)

SECTIONS to confirm (4): Verse 1 (L2-L5), Chorus (L8-L11), Verse 2 (L14-L17), Chorus (L20-L23)

Review the whole song now.
```

### Raw response

```text
[REVIEW.1]
pair: P1
verdict: agree
reason: The final sounds /an/ are pronounced identically in both "ควัน" and "นั้น".

[REVIEW.2]
pair: P2
verdict: agree
reason: The vowel /u/ and final /n/ in "ยืม" are very close to the vowel /u/ and final /n/ in "คืน".

[REVIEW.3]
pair: P3
verdict: agree
reason: The vowel /a/ and final /n/ in "นั้น" are pronounced identically to the vowel /a/ and final /n/ in "ร้าน".

[REVIEW.4]
pair: P4
verdict: agree
reason: The vowel /u/ and final /n/ in "ยืน" are pronounced identically to the vowel /u/ and final /n/ in "กลืน".

[ADD.1]
relation: exact
placement: end_to_end
confidence: high
reason: The ending "เธอ" (L8) rhymes perfectly with "เธอ" (L10) based on the pronunciation of the vowel /eo/ and final consonant /ka/.

[ADD.2]
relation: exact
placement: end_to_end
confidence: high
reason: The ending "เปลี่ยนไป" (L9) rhymes perfectly with "เปลี่ยนไป" (L11) based on the pronunciation of the diphthong /ai/ and final consonant /ka/.

[ADD.3]
relation: exact
placement: end_to_end
confidence: high
reason: The ending "เธอ" (L10) rhymes perfectly with "เธอ" (L20) based on the pronunciation of the vowel /eo/ and final consonant /ka/.

[ADD.4]
relation: exact
placement: end_to_end
confidence: high
reason: The ending "เปลี่ยนไป" (L11) rhymes perfectly with "เปลี่ยนไป" (L21) based on the diphthong /ai/ and final consonant /ka/.

[ADD.5]
relation: exact
placement: end_to_end
confidence: high
reason: The ending "เธอ" (L20) rhymes perfectly with "เธอ" (L22) based on the pronunciation of the vowel /eo/ and final consonant /ka/.

[ADD.6]
relation: exact
placement: end_to_end
confidence: high
reason: The ending "เปลี่ยนไป" (L21) rhymes perfectly with "เปลี่ยนไป" (L23) based on the pronunciation of the diphthong /ai/ and final consonant /ka/.

[DISPUTE.1]
target: R1
python_says: "ไป" (L9) / "ไป" (L11)
you_say: "ไป" (L9) / "ไป" (L11)
reason: Python correctly identified this as a repetition, but it is also a perfect rhyme, so it should be noted as both.

[DISPUTE.2]
target: R2
python_says: "ไป" (L11) / "ไป" (L21)
you_say: "ไป" (L11) / "ไป" (L21)
reason: Python correctly identified this as a repetition, but it is also a perfect rhyme, so it should be noted as both.

[DISPUTE.3]
target: R3
python_says: "ไป" (L21) / "ไป" (L23)
you_say: "ไป" (L21) / "ไป" (L23)
reason: Python correctly identified this as a repetition, but it is also a perfect rhyme, so it should be noted as both.

[SECTION.1]
name: Verse 1
checked: yes
note: The internal rhyme of "กลืน" in L16 rhymes with the ending "ยืม" (L4) which Python missed as an end-to-end rhyme.

[SECTION.2]
name: Chorus
checked: yes
note: Python correctly found the primary rhyming pairs for "เธอ" and "เปลี่ยนไป".

[SECTION.3]
name: Verse 2
checked: yes
note: The internal rhyme "กลืน" (L16) pairs perfectly with the end rhyme "ยืม" (L4) across sections.

[SECTION.4]
name: Chorus
checked: yes
note: The final rhyme "ตลอดไป" (L11) pairs perfectly with "ตลอดไป" (L23) which Python found as a repeat, but it is also an end-to-end rhyme.

[DONE]
```


## ภาคผนวก C-S1 — ผล Merge

สถานะ: {'verified': 8, 'candidate': 0, 'disputed': 1, 'rejected': 0} | coverage: {'reviewed': 9, 'python_pairs': 9, 'sections_checked': 5, 'sections': 5, 'unknown_refs': 0} | parse issues: []

### Evidence ที่ Songwriter จะได้ (prototype `evidence_text_v2`)

```text
RHYME EVIDENCE (Python sound check first, then a whole-song AI review checked by Python. VERIFIED = Python confirmed the sound. CANDIDATE = proposed but Python could not confirm. DISPUTED = Python confirmed the sound but the reviewer says it is not a rhyme when sung. Do not present candidate or disputed pairs as settled rhymes.)
- Verse 1 (lines 2-5)
    VERIFIED: L2 บ้าน <-> L3 นาน [exact, end_to_end, high] | L4 เก่า <-> L5 เบา [exact, end_to_end, high]
    DISPUTED: L3 นาน <-> L20 ช่าง [near, end_to_end, low]
- Pre-Chorus (lines 8-9)
    no rhyme pair confirmed in this section
- Chorus (lines 12-15)
    VERIFIED: L14 ถ้า <-> ฟ้า (inside the line) [exact, internal, high] | L14 ไห้ <-> L15 ใส [exact, end_to_end, high] | L15 ใส <-> L18 ไร [exact, end_to_end, medium]
- Verse 2 (lines 18-21)
    VERIFIED: L18 ไร <-> L26 ไห้ [exact, end_to_end, medium]
- Chorus (lines 24-27)
    VERIFIED: L26 ถ้า <-> ฟ้า (inside the line) [exact, internal, high] | L26 ไห้ <-> L27 ใส [exact, end_to_end, high]
- Bridge (lines 30-31)
    no rhyme pair confirmed in this section
```

### Disagreement / Blocked

```json
{
 "disagreements": [
  {
   "target": "P2",
   "type": "dispute",
   "python": "near (L3 นาน <-> L20 ช่าง)",
   "llm": "exact (L3 นาน <-> L20 ช่าง)",
   "reason": "The final consonant sound /n/ in นาน is a very close match to the final /ŋ/ in ช่าง in Thai pronunciation contexts.",
   "resolved": "disputed (not decided)"
  },
  {
   "target": "U1",
   "type": "dispute",
   "python": "could not read (L9)",
   "llm": "? (L9)",
   "reason": "Python likely missed the inherent vowel sound or the final consonant sound of the word on L9.",
   "resolved": "kept for debug only"
  }
 ],
 "blocked": [
  {
   "kind": "add_bad_endpoints",
   "ref": "ADD.1",
   "why": "0 endpoints"
  },
  {
   "kind": "add_bad_endpoints",
   "ref": "ADD.2",
   "why": "0 endpoints"
  },
  {
   "kind": "add_bad_endpoints",
   "ref": "ADD.3",
   "why": "0 endpoints"
  },
  {
   "kind": "add_bad_endpoints",
   "ref": "ADD.4",
   "why": "0 endpoints"
  },
  {
   "kind": "add_bad_endpoints",
   "ref": "ADD.5",
   "why": "0 endpoints"
  },
  {
   "kind": "add_bad_endpoints",
   "ref": "ADD.6",
   "why": "0 endpoints"
  },
  {
   "kind": "add_bad_endpoints",
   "ref": "ADD.7",
   "why": "0 endpoints"
  },
  {
   "kind": "add_bad_endpoints",
   "ref": "ADD.8",
   "why": "0 endpoints"
  },
  {
   "kind": "add_bad_endpoints",
   "ref": "ADD.9",
   "why": "0 endpoints"
  }
 ]
}
```


## ภาคผนวก C-S2 — ผล Merge

สถานะ: {'verified': 0, 'candidate': 0, 'disputed': 1, 'rejected': 4} | coverage: {'reviewed': 1, 'python_pairs': 1, 'sections_checked': 3, 'sections': 3, 'unknown_refs': 3} | parse issues: []

### Evidence ที่ Songwriter จะได้ (prototype `evidence_text_v2`)

```text
RHYME EVIDENCE (Python sound check first, then a whole-song AI review checked by Python. VERIFIED = Python confirmed the sound. CANDIDATE = proposed but Python could not confirm. DISPUTED = Python confirmed the sound but the reviewer says it is not a rhyme when sung. Do not present candidate or disputed pairs as settled rhymes.)
- Verse 1 (lines 2-5)
    DISPUTED: L5 ไร <-> L13 ไหม [exact, end_to_end, low]
- Chorus (lines 8-10)
    no rhyme pair confirmed in this section
- Verse 2 (lines 13-16)
    no rhyme pair confirmed in this section
- Chorus (lines 19-21)
    no rhyme pair confirmed in this section
NOTE: 4 proposed pair(s) were rejected by the checks and are not evidence.
```

### Disagreement / Blocked

```json
{
 "disagreements": [
  {
   "target": "P1",
   "type": "dispute",
   "python": "exact | end_to_end | L5 ไร <-> L13 ไหม | ไอ+กา | distant",
   "llm": "unsure | end_to_end | L5 ไร <-> L13 ไหม | ไอ+กา | close",
   "reason": "The sound is very close, leaning towards 'unsure' rather than a definitive 'exact' match based on phonetic nuances.",
   "resolved": "disputed (not decided)"
  },
  {
   "target": "P2",
   "type": "dispute",
   "python": "exact | end_to_end | L8 ตัวเอง <-> L10 ตัวเอง | เอ+กง | distant",
   "llm": "exact | end_to_end | L8 ตัวเอง <-> L10 ตัวเอง | เอ+กง | exact",
   "reason": "Python labeled this as 'distant' even though it is an exact match for the same word.",
   "resolved": "unknown target, kept for debug only"
  },
  {
   "target": "P3",
   "type": "dispute",
   "python": "exact | end_to_end | L10 ตัวเอง <-> L19 ตัวเอง | เอ+กง | distant",
   "llm": "exact | end_to_end | L10 ตัวเอง <-> L19 ตัวเอง | เอ+กง | exact",
   "reason": "Python labeled this as 'distant' despite it being an exact match.",
   "resolved": "unknown target, kept for debug only"
  },
  {
   "target": "P4",
   "type": "dispute",
   "python": "exact | end_to_end | L19 ตัวเอง <-> L21 ตัวเอง | เอ+กง | distant",
   "llm": "exact | end_to_end | L19 ตัวเอง <-> L21 ตัวเอง | เอ+กง | exact",
   "reason": "Python labeled this as 'distant' even though it is an exact match.",
   "resolved": "unknown target, kept for debug only"
  },
  {
   "target": "ADD.1",
   "type": "added_pair_rejected",
   "python": "Python sound check says not a vowel rhyme: เมล์ เอ+กา · ไร ไอ+กา",
   "llm": "exact",
   "reason": "L2 ends in 'เอ+กา' (met), L5 ends in 'ไอ+กา' (rai), so L2's sound is slightly different but the final 'กา' sound is shared.",
   "resolved": "rejected"
  },
  {
   "target": "ADD.3",
   "type": "added_pair_rejected",
   "python": "Python sound check says not a vowel rhyme: เคย เออ+เกย · แชต แอ+กด",
   "llm": "exact",
   "reason": "L4 ends in 'เออ+เกย' (kher), and L15 contains 'แชต' (chaet) which shares the 'ae' vowel sound, but the final consonant is different ('y' vs 't'). This is a near rhyme.",
   "resolved": "rejected"
  }
 ],
 "blocked": [
  {
   "kind": "review_unknown_pair",
   "ref": "P2"
  },
  {
   "kind": "review_unknown_pair",
   "ref": "P3"
  },
  {
   "kind": "review_unknown_pair",
   "ref": "P4"
  },
  {
   "kind": "dispute_unknown_target",
   "ref": "P2"
  },
  {
   "kind": "dispute_unknown_target",
   "ref": "P3"
  },
  {
   "kind": "dispute_unknown_target",
   "ref": "P4"
  },
  {
   "kind": "hallucination_or_bad_position",
   "ref": "ADD.2",
   "why": "L14 'ตาม': word is not at the end of the line",
   "claimed": [
    [
     3,
     "วัน"
    ],
    [
     14,
     "ตาม"
    ]
   ]
  },
  {
   "kind": "hallucination_or_bad_position",
   "ref": "ADD.5",
   "why": "L14 'ตาม': word is not at the end of the line",
   "claimed": [
    [
     14,
     "ตาม"
    ],
    [
     16,
     "รอบ"
    ]
   ]
  }
 ]
}
```


## ภาคผนวก C-S3 — ผล Merge

สถานะ: {'verified': 4, 'candidate': 0, 'disputed': 0, 'rejected': 3} | coverage: {'reviewed': 4, 'python_pairs': 4, 'sections_checked': 3, 'sections': 3, 'unknown_refs': 0} | parse issues: []

### Evidence ที่ Songwriter จะได้ (prototype `evidence_text_v2`)

```text
RHYME EVIDENCE (Python sound check first, then a whole-song AI review checked by Python. VERIFIED = Python confirmed the sound. CANDIDATE = proposed but Python could not confirm. DISPUTED = Python confirmed the sound but the reviewer says it is not a rhyme when sung. Do not present candidate or disputed pairs as settled rhymes.)
- Verse 1 (lines 2-5)
    VERIFIED: L3 ควัน <-> L5 นั้น [exact, end_to_end, high]
    VERIFIED BUT DISTANT (low confidence): L4 ยืม <-> L17 คืน [near, end_to_end, low] | L5 นั้น <-> L16 ร้าน [near, end_to_end, low]
- Chorus (lines 8-11)
    REPETITION (not a rhyme): L9 ไป <-> L11 ไป [exact, end_to_end, high] | L11 ไป <-> L21 ไป [exact, end_to_end, high]
- Verse 2 (lines 14-17)
    VERIFIED: L16 ยืน <-> กลืน (inside the line) [exact, internal, high]
- Chorus (lines 20-23)
    REPETITION (not a rhyme): L21 ไป <-> L23 ไป [exact, end_to_end, high]
```

### Disagreement / Blocked

```json
{
 "disagreements": [
  {
   "target": "R1",
   "type": "dispute",
   "python": "\"ไป\" (L9) / \"ไป\" (L11)",
   "llm": "\"ไป\" (L9) / \"ไป\" (L11)",
   "reason": "Python correctly identified this as a repetition, but it is also a perfect rhyme, so it should be noted as both.",
   "resolved": "kept for debug only"
  },
  {
   "target": "R2",
   "type": "dispute",
   "python": "\"ไป\" (L11) / \"ไป\" (L21)",
   "llm": "\"ไป\" (L11) / \"ไป\" (L21)",
   "reason": "Python correctly identified this as a repetition, but it is also a perfect rhyme, so it should be noted as both.",
   "resolved": "kept for debug only"
  },
  {
   "target": "R3",
   "type": "dispute",
   "python": "\"ไป\" (L21) / \"ไป\" (L23)",
   "llm": "\"ไป\" (L21) / \"ไป\" (L23)",
   "reason": "Python correctly identified this as a repetition, but it is also a perfect rhyme, so it should be noted as both.",
   "resolved": "kept for debug only"
  }
 ],
 "blocked": [
  {
   "kind": "add_bad_endpoints",
   "ref": "ADD.1",
   "why": "0 endpoints"
  },
  {
   "kind": "add_bad_endpoints",
   "ref": "ADD.2",
   "why": "0 endpoints"
  },
  {
   "kind": "add_bad_endpoints",
   "ref": "ADD.3",
   "why": "0 endpoints"
  },
  {
   "kind": "add_bad_endpoints",
   "ref": "ADD.4",
   "why": "0 endpoints"
  },
  {
   "kind": "add_bad_endpoints",
   "ref": "ADD.5",
   "why": "0 endpoints"
  },
  {
   "kind": "add_bad_endpoints",
   "ref": "ADD.6",
   "why": "0 endpoints"
  }
 ]
}
```


## ภาคผนวก D — Offline regression (`test_offline.py`)

| Case | ผล | รายละเอียด |
|---|---|---|
| good answer parsed | PASS | [] |
| Python-verified pairs not lost when the reviewer agrees | PASS |  |
| valid new pair verified by Python (not by the reviewer's confidence) | PASS |  |
| duplicate pair merged, not added twice | PASS |  |
| invented word blocked | PASS |  |
| tag line / fragment blocked | PASS |  |
| different sound rejected (not verified) | PASS |  |
| repetition rejected as repetition | PASS |  |
| section coverage counted | PASS | {'reviewed': 9, 'python_pairs': 9, 'sections_checked': 5, 'sections': 5, 'unknown_refs': 0 |
| rejected pairs never listed as rhymes in Songwriter evidence | PASS |  |
| production evidence_text can read the merged evidence dict (shape compatible) | PASS |  |
| LLM 'wrong' -> disputed (kept, not verified, not dropped) | PASS |  |
| LLM dispute -> disputed + disagreement kept | PASS |  |
| disputed pair not in Songwriter 'VERIFIED' list | PASS |  |
| broken answer: empty | PASS | issues=['no [DONE] line: complete tags above were still read'] |
| broken answer: garbage | PASS | issues=['no [DONE] line: complete tags above were still read', 'text before the first tag  |
| broken answer: truncated (no DONE, half a tag) | PASS | issues=['no [DONE] line: complete tags above were still read'] |
| broken answer: json instead of tags | PASS | issues=['no [DONE] line: complete tags above were still read', 'text before the first tag  |
| broken answer: unknown tag + repeated tag | PASS | issues=['[FOO.1] is not a tag the reviewer may use (ignored)', '[REVIEW.1] repeated; the f |
| reviewer failure -> python only still produces evidence | PASS |  |
| review of an unknown pair id is ignored and logged | PASS |  |
| no-rhyme song: reviewer's wrong 'exact' pairs are not verified | PASS | [('ADD.1', 'rejected'), ('P1', 'verified'), ('ADD.2', 'rejected'), ('ADD.3', 'rejected')] |
| no-rhyme song: the lone distant Python pair is flagged low confidence | PASS |  |
| repeated word 'ไป' is repetition, not a rhyme | PASS |  |
| no edit / apply action fields anywhere in the merged result | PASS |  |
| engine does not change the lyrics | PASS |  |
