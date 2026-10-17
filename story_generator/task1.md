Brief — Simplify State Architecture: Let Fill Resolve Continuity Shot-by-Shot

เป้าหมาย:
ลด token ของ Outline A โดยไม่เสีย continuity

แนวใหม่:
Outline A ไม่ต้องสร้าง full Clothing Arc + Physical State Plan ทั้งเรื่องอีก

==================================================
1. OUTLINE A ROLE
==================================================

Outline A รับผิดชอบแค่:

- Character Canon
- Scene Canon
- Props Canon
- Events

Events ต้องเป็น canonical timeline ว่า WHAT happens and when

ไม่ต้องเขียน:
- clothing_arc เต็มทุก E
- physical_state_plan เต็มทุก E
- SAME ซ้ำหลายสิบบรรทัด

==================================================
2. FILL BECOMES CONTINUITY-AWARE
==================================================

Fill ของ Shot N ได้:

- Full Story
- Current Event En
- Previous Resolved State
- Character Canon
- Scene Canon
- Props Canon
- previous shot context เล็กน้อย

แล้ว Fill สร้าง state ของ shot ปัจจุบันเอง

==================================================
3. CORE RULE
==================================================

No cause → INHERIT previous state

Current event clearly changes state → UPDATE

Never reset from Character Sheet / Scene default

Never invent:
- re-dressing
- prop movement
- character teleport
- spacing change
- body-state change

โดยไม่มี cause ใน current event/story

==================================================
4. PREVIOUS RESOLVED STATE
==================================================

เก็บ state หลัง Shot N เช่น:

Lalin:
- location/zone
- clothing
- body state
- props held/placed

Thana:
- location/zone
- clothing
- body state
- props held/placed

spacing:
- FAR/APART/NEAR

แล้วส่ง state นี้เข้า Fill ของ Shot N+1

==================================================
5. PYTHON = STATE MEMORY
==================================================

หลัง Fill คืน shot:

Python extract/resolve:

place
spacing
clothing_state
prop_state
persistent body state

เก็บเป็น Resolved State N

จากนั้นใช้เป็น previous state ของ shot ต่อไป

==================================================
6. FILL AUTHORITY
==================================================

Fill อ่าน Full Story เพื่อเข้าใจบริบทและอนาคตได้

แต่ state transition ของ Shot N ต้องเกิดจาก:
- Current Event En
- หรือ transition ที่ source ระบุชัดใน beat นี้

ห้ามใช้เหตุการณ์อนาคตมาทำ state เปลี่ยนก่อนเวลา

==================================================
7. KEEP HARD CANONICAL ENFORCEMENT
==================================================

ยังเก็บ Python enforcement เดิม:

- roster
- clothing owner
- prop owner/hand/location
- spacing
- visibility
- prompt syntax cleanup

Checker = audit
ไม่ใช่ตัวสร้าง continuity ใหม่

==================================================
8. OPTIONAL TRANSITION HINTS
==================================================

ถ้าจำเป็นจริง ๆ Outline A อาจคืนเฉพาะ transition สำคัญแบบสั้น:

E6 MOVE Lalin → MidPlatform
E7 PLACE handbag
E9 MOVE Thana → toward Lalin
E10 PLACE umbrella → bench
E11 REMOVE coat

แต่ไม่ต้องคืน full resolved state ทุก event

Optional only — ไม่ใช่ full arc

==================================================
9. FALLBACK
==================================================

ถ้า Fill ไม่แน่ใจ:

INHERIT previous state

ดีกว่า invent/reset

==================================================
10. DO NOT TOUCH
==================================================

ยังไม่รื้อ:
- Event extraction
- Character Canon
- Prompt Checker
- Director
- Krea2 prompt format
- canonical Python repair

Patch นี้เปลี่ยนเฉพาะ state-planning responsibility

==================================================
FINAL ARCHITECTURE
==================================================

Outline A
→ Canon + Events

Fill Shot N
← Full Story
← Event N
← Previous Resolved State
→ Shot + Current State

Python
→ validate
→ save Resolved State N

Checker
→ audit final prompt

หลักสำคัญ:

Outline decides WHAT happens.
Fill decides WHAT IS TRUE in this shot from previous state + current event.
Python remembers continuity.