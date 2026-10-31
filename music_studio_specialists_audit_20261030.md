# WorkflowPad Music Studio — AI Specialists Audit
วันที่ตรวจ: 30 ตุลาคม 2026
สถานะ: **Code audit + isolated mock reproductions; ไม่ได้แก้ source และไม่ได้ทดสอบ GUI/LLM จริง**

## Scope / Files
ตรวจแหล่งข้อมูลที่ผู้ใช้อัปโหลด: `music_studio(20261030-070531).py` (7279 lines), `specialist_engine.py`, `llm_chat(7).py`, `lyric_analysis(4).py`, `lyric_rhyme(5).py`, `lyric_rhythm(4).py`, `specialist_trace.py`, `specialists.py`, `template_arrange.py`, และไฟล์ผู้เชี่ยวชาญ `.md` ทั้งห้า

ไม่ได้รับโมดูล dependency รุ่นปัจจุบันครบทุกตัว (`model.py`, `store.py`, `lyric_actions.py`, `lyric_review.py`, `thai_rhyme.py`, Flet runtime/ComfyUI workflow และสถานะจริงของโปรแกรม) จึงยังรับรองการทำงานทั้งแอปไม่ได้

## P0 — Fix first

### A01 — Chat รองรับ Proposal หนึ่งก้อนต่อข้อความ; ใช้ได้ครั้งเดียว
**Code:** `music_studio...py:2787–2797, 3015–3053, 3287–3333`; `llm_chat(7).py:4–8, 58–83, 108–129`.

`REPLY/PROPOSAL/END` คืน `proposal: str` เพียงหนึ่งรายการ, UI มี Apply หนึ่งปุ่ม และเมื่อลงมือจะกำหนด `m.applied=True`; ปุ่มถูก disabled จึงไม่สามารถ Apply ข้อเสนอ 2–3 จุดจากคำตอบเดียวทีละรายการได้ (แตกต่างจาก Report Finding ที่แยก card). **เป็นข้อจำกัดโครงสร้าง ไม่ใช่แค่ prompt**.

**Fix:** แยก `ChatResponse` ออกเป็นข้อความปรึกษา (`answer`) กับ `edit_operations[]` ที่มี id, mode (replace/insert/tag/section), exact original, proposed text, line/section anchor, confidence/status, rationale. ให้แต่ละ operation มี Preview/Apply/Ignore และสถานะของตัวเอง; อย่าปิดทุก operation เพราะ apply หนึ่งรายการ. ใช้ `Proposal` แบบเดิมเป็น compatibility pathway สำหรับ AI Actions เก่าจนกว่าจะ migrate.

### A02 — คำอธิบายปนใน Proposal หลุดผ่าน Guard ได้
**Code:** `specialist_engine.py:927–965`; `music_studio...py:3022–3053`.

`clean_proposal` บล็อก Markdown บางรูปแบบ แต่ข้อความธรรมดา เช่น `ข้อเสนอที่ 1: ลองเปลี่ยนประโยคนี้เป็น แสงดาวเต็มฟ้า` และ `ปรับคำให้ไพเราะมากขึ้น\nแสงดาวเต็มฟ้า` ยังถูกยอมรับใน mock. หาก original ไม่มี tag หรือ AI คืนแท็กครบ ก็มีความเสี่ยงนำคำอธิบายเข้า Editor.

**Fix:** Parse typed edit records (fields ไม่ใช่ free-text block) + exact match/range guards + `kind` and `user_intent` validation. `reason` กับ `replacement` ต้องเป็นคนละ field; หากไม่ยืนยันว่าเป็น lyric-only ให้แสดงเป็นข้อความและไม่เสนอ Apply. เพิ่ม unit tests ของ Thai prose, numbered advice, Markdown, mixed output.

### A03 — ผู้เชี่ยวชาญรายงานไม่ได้ต่อ Chat History เดิมจริง
**Code:** `music_studio...py:2870–2885` (`chat=[] if task else list(S.chat)`), `music_studio...py:2962–3004` (chat supplies `hist`), `specialist_engine.py:259–322`.

Report, action, prosody jobs เรียก `spec_ask(..., task='...')` ซึ่งส่ง `[]` เข้า `SE.prepare` ทุกครั้ง จึงไม่เห็นบทสนทนาก่อน Analyze ในคำขอรายงานแม้ใน session identity เดียวกัน. หลัง Analyze, Chat อาจเห็น `latest_report` แบบย่อ แต่ไม่สมบูรณ์. **ผิด requirement Chat→Analyze→Chat Continuity**.

**Fix:** เก็บ event stream ต่อ Song+Sheet+Specialist (`user_message`, `assistant_message`, `analysis_request`, `analysis_result`, `revision_request`, `revision_result`). ทุกคำขอดึง relevant events ของคนเดียวกัน + versioned lyric snapshot; คำตอบรายงานต้องบันทึกลง stream ก่อนอนุญาต Chat ต่อ. ประเมิน context ให้ใช้ selective retrieval/summarization แทน `[]`.

### A04 — Priority Improvements ทำข้อเสนอคนละเรื่องในบรรทัดเดียวกันหาย
**Code:** `specialist_engine.py:1061–1083`.

`aggregate` dedup ด้วย `(first,last)` เท่านั้น. Mock: Song Editor + Lyric Craft Advisor ต่างเสนอแก้บรรทัด 2 คนละเรื่อง กลับเหลือ card เดียว; อีกคนถูกเพิ่มเพียง `also_by` ทั้งที่คำแนะนำต่างกัน. แม้ข้อมูลรายงานต้นทางอาจยังอยู่ รายการรวม/Apply หาย.

**Fix:** Dedup เฉพาะ semantic duplicate (same operation+original+replacement or explicit equivalence), เก็บ distinct findings แม้เป็นบรรทัดเดียวกัน, จัดกลุ่ม `same_location` ไม่ทิ้ง. แสดง conflict หากข้อเสนอขัดกัน.

### A05 — Chat ที่ไม่มี selection แก้หนึ่งบรรทัดไม่ผ่าน Apply
**Code:** `music_studio...py:2968–2981, 3028–3049`; `specialist_engine.py:893–925, 947–965`.

Chat ไม่มี selection ถูกกำหนด scope `all`. ระบบคาด `PROPOSAL` เป็นเพลงทั้งเพลงพร้อม tags หรือบาง section ตามแบบที่ merge ได้; ถ้าโมเดลเสนอแก้เพียงหนึ่งวรรคโดยไม่ส่ง section tag จะถูก `proposal_problem` ปฏิเสธว่า drop `[Verse 1]`, `[Chorus]`. Mock confirmed.

**Fix:** Chat edit target must be explicit: line/range/section anchored to current snapshot. ไม่บังคับให้โมเดลส่งทั้งเพลงเพื่อแก้หนึ่งบรรทัด; full song replacement ต้องเป็น explicit user action เท่านั้น.

## P1 — High priority

### A06 — ความต่อเนื่อง Prosody แค่สรุปตัวเลข ไม่ได้รู้รายละเอียดที่ตรวจ
**Code:** `music_studio...py:5199–5242`; `specialist_engine.py:235–245, 259–322`.

`make_prosody_report` report summary บอกจำนวนพยางค์/จำนวน rhyme pairs และ flow remarks เท่านั้น; `report_text` จำกัด 1500 chars และไม่ serialize syllable units/rhyme pairs เป็นบริบท Chat จึงถามต่อว่า `ทำไม ดาว–หนาว เป็นสัมผัส` แล้วอาจต้องคิดใหม่ ไม่ใช่อ้างหลักฐานจากรายงานของตัวเอง.

**Fix:** store typed prosody artifacts (line segmentation, pair & classification, reasons, source lyric_hash). For specialist chat retrieve referenced finding and relevant full evidence, not just truncated summary. Avoid forced automatic LLM recollection.

### A07 — Strict hand-off policy ทำให้ Specialist คุยไม่เป็นธรรมชาติ
**Code:** `specialist_engine.py:323–405`; `music_studio...py:3017–3020`; `.md` specialist roles.

`team_block` เขียนว่าเมื่อคำถามอยู่ใน field ของ teammate ให้ `do NOT answer`, และ `FIELD: other` ถูก code ลบทิ้ง proposal ทันที. คำถามที่เกี่ยวทั้งคำร้อง สัมผัส และ structure อาจถูกส่งต่อซ้ำหรือหลุดสิ่งที่ผู้ใช้ขอ.

**Fix:** Answer-first for questions intersecting one's expertise; cross-domain advice may be brief with uncertainty. Hand-off only for **explicitly out-of-scope actions**. Show suggested expert without deleting answer or candidate automatically unless unsafe to apply. Test Thai ambiguous requests, e.g. `ช่วยแก้ประโยคนี้ให้คล้องจองและสื่อความหมายเดิม`.

### A08 — Context Budget สามารถต่ำกว่า 0 แต่ยังส่ง Request
**Code:** `specialist_engine.py:259–322`.

`budget` อาจติดลบถ้า system+few-shot+lyrics ยาว; code ไม่ reject. Isolated mock input est=15,400 tokens with n_ctx=8192 ผ่าน `prepare` ไม่มี error. Needs split/overflow before network call.

**Fix:** hard guard `estimated_total + output_reserve <= supported_limit`; if not, refuse with diagnostic or chunk by section (keeping context and evidence). Count example tokens, latest report, request/reply ceilings. Validate per backend and per model.

### A09 — Prompt/Backend/Model Change ไม่สร้าง generation ครบ
**Code:** `specialist_engine.py:264–277`.

Generation reset checks only `sess.prompt_hash != prompt.hash`; backend and model are overwritten in-place. This conflicts with rule separating model/session configuration. **Risk** of past session summary being treated as native continuity across models; code already replays context statelessly, so data may still be available, but version/audit identity inaccurate.

**Fix:** generation key `(prompt_hash, backend, model, session_format_version)`; explicit carry summary and report references.

### A10 — แชต/รายงานไม่ครอบคลุม Lifecycle เมื่อเปลี่ยนเพลง/Sheet และงานค้าง
**Code:** `music_studio...py:5117–5138, 5246–5266`.

`start_report` captures song/text/sheet but `run_report` rebuilds `snap = spec_snapshot()` after scheduling, reading global state again. เมื่อสลับแท็บทันทีอาจ abort เงียบหรือ progress ผูกผิด. การตรวจ state แยกในบางขั้น. **Potential race; UI runtime repro not yet done.**

**Fix:** create immutable `snap` synchronously at click and pass it unchanged across whole job; request token, cancel, stale handling, session locks across all tasks, all state updates guarded by request id and song/sheet.

### A11 — `Clear chat` ล้างทั้งเพลง ไม่ใช่ Specialist ปัจจุบัน
**Code:** `music_studio...py:3336–3347`.

กด Clear Chat หลังเลือกคนหนึ่งจะตั้ง `S.chat=[]`, ลบไฟล์แชตทั้งเพลง จึงลบทุก Specialist/Sheet ในเพลงเดียวกัน; reports in different file may remain. ต้องถามชัดหรือให้ Clear Current Specialist เป็น default.

### A12 — ไม่มีการแยกสถานะข้อเสนอหลากหลายแบบข้าม Report/Chat
**Code:** `music_studio...py:2782–2797, 3287–3330, 4866–4950`; `specialist_engine.py:790–802, 998–1020`.

Report Findings มี individual Apply, แต่ Chat ใช้ `m.applied` อย่างเดียว; UI/metadata ไม่สอดคล้อง. `m.applied` อาจยังเป็น True หลัง Undo ของ Editor, ทำให้ผู้ใช้ Apply ซ้ำไม่ได้แม้กลับไป Original. **Need design decision** whether allow re-apply after Undo, but should track exact edited revision, not permanent boolean.

### A13 — Parser/report สูญเสียคำแนะนำที่เป็นภาษาธรรมชาติ
**Code:** `specialist_engine.py:509–615, 773–801, 1021–1059`.

`_advice_not_lyrics` discards suggestion prefixed with `ลอง`, `เปลี่ยน`, `ควร` as non-applicable (safe, but should isolate quoted candidate). Report parses only up to MAX_FINDINGS=5 for most roles and MAX_ARCH_FINDINGS=8 for Architect. If user expects multi-step edits, needs per-action structured output and displayed dropped reasons.

### A14 — Trace เก็บเนื้อเพลง/คำถาม/คำตอบละเอียดบนดิสก์
**Code:** `specialist_trace.py:1–60`, `music_studio...py:3064–3065, 5179–5181`.

Good for debug but logging is by default and includes full lyrics and raw LLM output; privacy/disk retention preference needs disclosure and optional opt-in or redaction. No API keys written by module. Not a confirmed leak to internet.

## Positive foundations worth keeping
- Individual reports stored with song/sheet and prompt hash; `specialist_engine.py:101–209`.
- Report findings use exact lyric evidence and safer range validation; `specialist_engine.py:967–1059`.
- Existing `fd_apply` supports applying **multiple different report cards** one by one; `music_studio...py:4920–4940`.
- Song Architect constructs section/tag changes deterministically from operations rather than letting LLM rewrite arbitrary lyric lines; `specialist_engine.py:616–772`.
- All nine Python files parse successfully with AST (static syntax validation).
- Off-song `append_chat` branch DOES append message before saving; `music_studio...py:2820–2831`. This specific branch is **not** a proven lost-message bug. Investigate broader concurrency separately.

## Mock / static tests executed
- AST parse: 9/9 PASS (syntax only)
- Dedup 2 distinct same-line findings → 1 visible in aggregate: **BUG reproduced**
- `clean_proposal` accepts unstructured Thai explanation in replacement: **BUG reproduced**
- Negative `prepare` budget with very long input does not raise: **BUG reproduced**
- One-line proposal without selection/section tags rejected for full song: **BUG reproduced**
- Quoted-option extraction can return 2 candidates: **working in isolated test**
- Report suggestion that starts `เปลี่ยน...` drops Apply field: **observed safeguard, usability issue**
- Tests use stubbed dependencies; **do not claim complete application/UI pass**

## Recommended implementation plan
1. **First**: reliable edit-operation contract and individual per-edit Apply. Keep legacy AI Actions until parity verified.
2. **Second**: repair specialist event history and report-to-chat evidence retrieval. Fix `spec_ask` empty history.
3. **Third**: revise cross-specialist collaboration/hand-off prompt, remove hard refusal for overlapping advice.
4. **Fourth**: fix aggregate semantics, per-specialist clear history, immutable request snapshot and context hard limits.
5. **Last**: run 1 controlled real Gemma4 case per critical scenario after static/mock tests; do not auto-call Grok.

## Acceptance scenarios (must pass)
1. Ask Lyric Craft Advisor `เสนอปรับสามวรรค แยกข้อเสนอพร้อมเหตุผล` → 3 independent Preview/Apply buttons; applying 1 leaves 2 available.
2. Ask `ปรับคำนี้...` without selecting text → target line identified/confirmed, NOT whole-song replacement.
3. LLM returns `REPLY` + multiple prose explanations → **no prose is sent to Lyrics**.
4. Ask Prosody before Analyze `อยากเน้นสัมผัส ใจ–ไป` → Analyze uses that preference, and chat afterward answers using exact prosody evidence.
5. Report on one line from both Editor and Craft Advisor → both findings remain.
6. User switches song/sheet mid-Analyze → result safely attached to origin snapshot, never wrong current sheet.
7. One Specialist's chat cleared → chats of the other three preserved.
8. Very long song + prompt examples → preflight bounds guard/chunk, no ComfyUI hang.
9. Prompt .md reload changes hash/version without corrupting saved conversations.
10. Actions / Song Architect structure / Music Director / Editor caret & Undo regressions all pass.

**No changes to user source files or live app were made during this audit.**
