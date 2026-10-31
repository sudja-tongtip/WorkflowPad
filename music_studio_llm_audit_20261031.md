# Audit: LLM ทุกเส้นทางใน WorkflowPad Music Studio — การสนทนาเป็นธรรมชาติ vs Structured Output
วันที่ 2026-10-31 · Audit Only (ไม่ได้แก้โค้ด, ไม่ได้แก้ `.md`, ไม่ได้เรียก LLM/ComfyUI) · หลักฐานมาจากโค้ดจริง, `chat.json`, `specialist_trace.jsonl` และไฟล์ session ของเพลง "พอก่อนค่ะแม่"

## 0. สรุป 6 บรรทัด
1. กรณี "เพิ่ม intro ให้หน่อย" ไม่ใช่ความผิดของ Gemma อย่างเดียว: **prompt สั่งให้ Song Architect ตอบ "ทั้งเพลง" ใน PROPOSAL** (`ARCHITECT_CHAT_RULE`) ทั้งที่งานจริงคือเพิ่ม section เดียว จึงได้เนื้อเพลงที่แต่งใหม่ 32–33 บรรทัด
2. **Python/UI ต่อข้อความภายในเข้าไปในแชต** ("32 line(s) … were left out", "Not offered for Apply: …" และตัว PROPOSAL ทั้งก้อน) แล้ว **บันทึกลงประวัติ** จึงถูกส่งกลับให้โมเดลเห็นในคำขอถัดไป
3. **Cue System ปฏิเสธทั้งข้อเสนอ** เมื่อมี cue เดียวที่ไม่อยู่ใน library (`architect_problem`) แทนที่จะแยก "ทิศทางดนตรีอิสระ" ออกจาก "cue ที่ Apply ได้"
4. **Context ใหญ่เกินจำเป็น**: คำสั่ง 3 คำ → คำขอ ~18,000 token (system ~14,400)
5. รูปแบบสีที่ prompt สอน (`{green}…{/}`) ทำให้ Gemma เขียนวงเล็บปีกกาเสียรูปและ LaTeX ปนในข้อความ (เห็นใน `chat.json` ทุกข้อความของ Architect)
6. ปัญหาเกือบทั้งหมดอยู่ที่ **ชั้นร่วม** (CHAT_CONTRACT, `prepare`, `ask_ai`, ประวัติ, UI) แก้ครั้งเดียวได้ ไม่ต้องแตะ `.md` ของแต่ละคน

## 1. ตารางหลัก (ตามที่ brief กำหนด)
Backend: ทุกเส้นทางใช้ `spec_llm` / `dr_ask_fn` แล้วแยกแค่ปลายทาง (`LC.ask_grok` หรือ `LC.ask_local`) **prompt ชุดเดียวกัน** ความต่างจริงมีแค่: Local = workflow ComfyUI `LLMSessionChatNode` (n_ctx 32,768 จากไฟล์ `gemma4_llm.json`, timeout 300 วิ, max_tokens จาก config 4096) · Grok = HTTP (n_ctx ที่ใช้วางแผน 32,768 ตายตัว, timeout 120 วิ, `reasoning_effort: low`, max_tokens 4096) ทั้งคู่ stateless: ส่งประวัติทั้งหมดซ้ำทุกคำขอ

| # | LLM / Specialist (ผู้เรียก) | Prompt Source | Conversation Handling | Output Format | ปัญหาที่พบจากโค้ดจริง | Severity |
|---|---|---|---|---|---|---|
| 1 | **Chat ของ Lyric Craft Advisor** (`ask_ai` @3206) | `lyric_craft_advisor.md` (มี Response Style) + `CHAT_CONTRACT` + `team_block` + `chat_field_rule` (ซ้ำอีกรอบท้าย user message) + `SONGWRITING_CRAFT` | replay ข้อความ **ที่แสดงบนจอ** (`chat_history` ใช้ `m.text`) ตัดที่ 1,500 ตัวอักษร/ข้อความ, เก่ากว่านั้นพับเป็น summary ด้วยโค้ด ไม่ใช้ LLM | **Mixed**: header `FIELD/KIND` + `REPLY` + `EDITS` + `PROPOSAL` + markup สี | system ~13,000 token ก่อนใส่เนื้อเพลง; กฎ "ALWAYS use EDITS/PROPOSAL" ในทุกคำถามที่แตะการเขียน; ประวัติมีข้อความระบบปน | Medium |
| 2 | **Chat ของ Song Architect** (`ask_ai`) | `song_architect.md` (ชี้ไปทาง *รายงาน* + few-shot แบบรายงาน, ไม่มีหัวข้อสนทนา) + `CHAT_CONTRACT` + `supported_cues_text` + `ARCHITECT_CHAT_RULE` | เหมือนข้อ 1 | Mixed และ **บังคับ whole-song PROPOSAL** | **ปัญหาหลักของกรณี intro** (ดูหัวข้อ 3) system ~14,400 token | **High** |
| 3 | **Chat ของ Song Editor** | `song_editor.md` + CONTRACT + team + field rule | เหมือนข้อ 1 | Mixed | ใช้ contract ชุดเดียวกัน (ซ้ำ ~2,900 token); ไม่พบกรณีพังจริงในข้อมูลที่มี | Medium |
| 4 | **Chat ของ Thai Prosody Expert** | `prosody_expert.md` + CONTRACT + team + field rule | เหมือนข้อ 1 + แนบ digest หลักฐานจากรายงาน | Mixed | เหมือนข้อ 3 | Medium |
| 5 | **Analyze Song** ของ 3 ผู้เชี่ยวชาญ (`run_spec_report` @5435, T=0.4) | `.md` + `analyze_task` / `analyze_user` (`specialist_engine.py`) | ใส่ประวัติแชตของคนนั้นด้วย (`spec_ask`: `task in ("", "analyze")`) + รายงานล่าสุด | **Structured** (รายงาน) + `op:` สำหรับ Architect | โครงสร้างเหมาะ แต่ข้อความภายในรั่วสู่ผู้ใช้เป็น "Partial/diagnostics" (แก้แล้วรอบก่อน) | Low |
| 6 | **Prosody engines** syl / rhyme / rhythm (`spec_ask("prosody", …)` @4250/4455/4664) | prompt ของเอนจิน (~1,850 token) **ผนวกกับ `prosody_expert.md` ทั้งไฟล์ (~4,800 token)** ผ่าน `prepare` | task ไม่ใช่ chat/analyze → ไม่มีประวัติ แต่ยังแนบ "latest report" | **Structured** (แถวตาราง) | persona + few-shot ของ *รายงาน* คิดเป็น **72%** ของ system prompt ในคำขอแยกพยางค์ → ช้าและเสี่ยงสับรูปแบบ (ที่มาของ est_in ~8,500) | Medium |
| 7 | **AI Actions** (`run_action` @3348: continue/emotion/phrasing/translate → Advisor, singability → Prosody) | `lyric_actions.SYSTEM_PROMPT` (บอกว่า "You are a songwriting assistant…") **ซ้อนบน** `.md` ของ specialist ผ่าน `prepare` | ไม่มีประวัติ (task `action:*`) ผลเข้าแชตเป็นข้อความ+proposal | Structured (REPLY/PROPOSAL) | มี "บทบาทสองชุด" ใน system เดียว (persona จาก md + "songwriting assistant" ของ action) | Low |
| 8 | **Review / Thesaurus** (`spec_ask("lyric_craft_advisor", …, "review"/"thes")` @5664/4906) | `lyric_review.py` / `lyric_thesaurus.py` `SYSTEM_PROMPT` + `.md` | ไม่มีประวัติ | Structured (แถว/marker) | ผลรวมเป็นรายการให้ UI ไม่ใช่การสนทนา เหมาะสมแล้ว | Low |
| 9 | **Song Structures / Arrange** (`spec_ask("song_architect", None, "arrange")` @6530) | `template_arrange.py` + `.md` | ไม่มีประวัติ | Structured | cue นอก library ถูกทิ้งพร้อมโน้ต (ตั้งใจ) | Low |
| 10 | **Music Director — Music Suggestion** (`spec_ask("music_director", …, "music_suggestion")` @6694) | `template_arrange.py` + `music_director.md` | ไม่มีประวัติ | Structured | คุยด้วยไม่ได้ในแชต (CHAT_IDS ไม่รวม) แต่ **handoff text ของทุกคนยังชี้ "ถาม Music Director"** → ผู้ใช้ไม่มีที่ถามต่อ | Medium |
| 11 | **AI Music Director (Generate)** (`dr_ask_fn` → `AD.analyze`) | `ai_music_director.SYSTEM` + `music_director.md` | ไม่มี session ไม่มีประวัติ (ถามครั้งเดียว) | Structured | รั่ว Basic เข้า prompt (**แก้แล้วในรอบ Patch 2**) | Low (แก้แล้ว) |
| 12 | **Basic Caption** (`dr_ask_fn` → `BC.compose_basic_caption`) | `basic_caption.SYSTEM` | ไม่มี | Structured (CAPTION marker) | เป็นงานแปลงข้อมูล ไม่ใช่สนทนา | Low |
| 13 | **Take Feedback** (@5780, ถามเรื่อง take ที่ไม่ชอบ) | `take_feedback.SYSTEM_PROMPT` | ไม่มี session (ถาม-ตอบครั้งเดียว) | Natural แต่บังคับ 3 ข้อตายตัว (WHAT THE DATA SAYS / MY GUESS / WHAT TO TRY) | **เป็นแม่แบบตายตัวทุกครั้ง** และไม่ใช้ session จึงถามต่อไม่ได้ต่อเนื่อง | Medium |

## 2. กรณีจริง: "เพิ่ม intro ให้หน่อย" (2026-10-31 02:32:48, Song Architect, Local Gemma)
หลักฐาน: `chat.json` ข้อความ 38–41 และ trace `kind=chat` 02:33:08 / 02:34:11 (`steps`, `raw_answer`, `request_tail`)

สิ่งที่เกิดขึ้นตามลำดับ:
1. `ask_ai` ใส่ **ข้อความที่เลือกค้างอยู่** (`selected` = บทท้ายของ Verse 1 4 บรรทัด) ลงใน `SELECTED TEXT (the proposal replaces only this)` ทั้งที่คำสั่งเป็นการเพิ่ม section ทั้งเพลง (UI มีป้าย "Selected: N chars" แต่ไม่มีการตีความ intent)
2. prompt ที่ส่ง = system ~14,400 token + เนื้อเพลง + รายงานล่าสุด + ประวัติ → `est_in = 18,077`
3. `ARCHITECT_CHAT_RULE` สั่ง: "เมื่อขอให้ add sections … ตอบ `KIND: lyrics` และใส่ **เนื้อเพลงของผู้ใช้ทั้งหมด** พร้อม tag ใน PROPOSAL; ทุกบรรทัดต้องคัดลอกตรงตัว" และ CHAT_CONTRACT สั่ง "ถ้าไม่มีข้อความที่เลือก PROPOSAL คือเนื้อเพลงฉบับสมบูรณ์" → โมเดลสร้างเพลงทั้งเพลงใหม่ (PROPOSAL 2,056 ตัวอักษร)
4. Gemma คัดลอก 44 บรรทัดไม่ตรง จึงเขียนบรรทัดใหม่ → `repair_architect_proposal` ตัดออก **33 บรรทัด** (trace `steps`: "33 line(s) that are not in the lyrics were taken out") แล้ว `ask_ai` ต่อข้อความ `"(32 line(s) the Song Architect made up were left out of the proposal; the rest can be applied.)"` เข้า `reply`
5. `proposal_problem → architect_problem` พบ cue `"bouncy bassline, rhythmic vocals"` ไม่อยู่ใน cue library → ปฏิเสธ **ทั้ง proposal** แล้วต่อ `"(Not offered for Apply: …)"` + **PROPOSAL ทั้งก้อน** เข้า `reply` (`music_studio.py:3305`)
6. ผลคือข้อความที่ผู้ใช้เห็นตรงกับที่แจ้ง: อธิบายยาว, เสนอ Verse/Pre-Chorus ที่ไม่ได้ขอ, "32 lines left out", cue ไม่รองรับ, คำเตือนเทคนิค
7. ทั้งหมดนี้ถูก **เก็บเป็นข้อความผู้ช่วย** (`m.text`) → `chat_history` ส่งกลับให้โมเดลในคำขอถัดไป (โมเดลเห็นตัวเองเขียนผิดรูปแบบ)
8. ข้อความที่ 2 ("หมายถึงดนตรี ก่อนเริ่มเพลง…"): `selected` = `[Intro - atmospheric, synth riser, dreamy]` (เหลือจากการ Apply ก่อนหน้า) โมเดลตอบว่า "ดูข้อเสนอได้ในส่วนของ EDITS" **โดยไม่มี EDITS** และ PROPOSAL เป็นประโยคเมตา → `guard: no proposal` ผู้ใช้เห็นคำสัญญาที่ไม่มีของ

### Root Cause (ตามหมวดที่ brief ถาม)
| หมวด | เกี่ยวข้อง? | หลักฐาน |
|---|---|---|
| 1. System Prompt | **ใช่ (หลัก)** | `ARCHITECT_CHAT_RULE` + `CHAT_CONTRACT` บรรทัด "complete new lyrics" บังคับทั้งเพลง; `chat_field_rule` สั่ง "Never answer such a request with talk only"; `song_architect.md` ไม่มีกฎการสนทนา (มีแต่ Response ของรายงาน) |
| 2. Context | **ใช่** | `selected` ที่ค้าง; system 14,400 token (`CHAT_CONTRACT` 2,938 + team 923 + field rule 1,084 ×2 + cues 1,032 + md 7,881); เนื้อเพลง 1,598 + รายงาน + ประวัติ |
| 3. Intent / Scope | **ใช่** | ไม่มีขั้นตัดสินว่า "เพิ่ม intro" = เพิ่ม section เดียว; selection ชนะโดยปริยาย |
| 4. Output Schema | **ใช่** | แชตของ Architect ไม่มี operation (ใช้ได้เฉพาะ report: `set_cue/add_section/…`) จึงต้องส่งทั้งเพลงเป็นข้อความ |
| 5. Parser / Validator | **ใช่** | `architect_problem` ทิ้งทั้งก้อนเพราะ cue เดียว; `repair_architect_proposal` ทำงานกับ output ที่ไม่ควรเกิด |
| 6. UI | **ใช่** | `ask_ai` ต่อโน้ตภายในเข้า `reply` (บรรทัด 3276/3296/3299/3301/3305/3313) และบันทึกลงประวัติ; `rich_text` ไม่กรองวงเล็บปีกกาเสียรูป (`}` ท้ายประโยค) |

ข้อสรุป: **ไม่มีหลักฐานว่าเป็นความผิดของโมเดลอย่างเดียว** — โมเดลทำตามสัญญาที่ prompt ให้ (ตอบทั้งเพลง) แล้วโปรแกรมแสดงผลการซ่อมของตัวเองให้ผู้ใช้เห็น

## 3. ปัญหาที่กระทบ "ทุก LLM" (แก้ที่ชั้นร่วม)
| รหัส | ปัญหา | ต้นเหตุ (ไฟล์/ฟังก์ชัน) | ประเภท | Severity |
|---|---|---|---|---|
| C1 | โน้ตระบบปนในข้อความสนทนาและถูกบันทึก/replay | `ask_ai` (3276–3313) ต่อ `reply`; `ChatMessage.text` = ข้อความที่แสดง; `chat_history` ใช้ `m.text` | Python/UI | **High** |
| C2 | CHAT_CONTRACT เป็นแม่แบบเดียว (REPLY/EDITS/PROPOSAL + header FIELD/KIND) บังคับทุกข้อความ ทั้งคำถามธรรมดา | `llm_chat.CHAT_CONTRACT`, `SE.chat_field_rule` | Prompt | **High** |
| C3 | "scope" มาจาก selection เสมอ ไม่ถามเจตนา | `ask_ai` (3217–3221), `LC.build_user_message` | Python | Medium |
| C4 | Context บวม + ซ้ำซ้อน (`chat_field_rule` อยู่ทั้ง system และ user message; team block/contract ซ้ำทุกครั้ง) | `spec_extra`, `chat_field_reminder`, `SE.prepare` | Prompt assembly | Medium (กระทบความเร็วและความสนใจ) |
| C5 | persona `.md` เต็มไฟล์ถูกแนบในเอนจินแถวตาราง (syl/rhyme/rhythm/action/review/thes) | `SE.prepare` → `SP.effective_system` | Prompt assembly | Medium |
| C6 | markup สีที่สอนไว้เสียรูป (`{green}…{/}\text{…}`, `}` ลอย, `{/ `) | `CHAT_CONTRACT` ส่วน COLOUR; `SE.plain_symbols` กรองได้บางแบบ | Prompt + UI | Medium |
| C7 | Cue/การเสนอถูกตัดสินแบบ "ทั้งก้อนหรือไม่เลย" | `architect_problem` (specialist_engine.py:857) | Validator | **High** (กระทบความสร้างสรรค์) |
| C8 | hand-off ชี้ไปยัง Music Director ที่คุยไม่ได้ในแชต | `team_block` สร้างจาก `SPECIALISTS` ทั้ง 5 แต่ `CHAT_IDS` มี 4 | Prompt data | Medium |
| C9 | คำสัญญาถึงสิ่งที่ไม่มี ("ดูใน EDITS") | `CHAT_CONTRACT`: "the REPLY only introduces them" + ไม่ตรวจว่ามี EDITS จริง | Prompt + Python | Medium |
| C10 | Take Feedback ตอบแม่แบบ 3 ข้อตายตัว และไม่มี session | `take_feedback.SYSTEM_PROMPT` | Prompt | Medium |

แยกตามชั้น: **Prompt** C2, C4, C5, C6, C8, C9, C10 · **Python/Parser/Validator** C1 (ส่วนที่ต่อโน้ต), C3, C7 · **UI** C1 (การแสดง/บันทึก), C6 (การเรนเดอร์) · **กระทบ LLM ทุกตัว**: C1, C2, C4, C6, C9 · **ไม่กระทบ Structured Analysis/Apply**: ข้อเสนอแก้ทั้งหมดด้านล่างไม่แตะ parser ของ report, EDITS, `apply_*`

## 4. Cue System: ทำไมเสนอ cue นอก library แล้ว Apply ไม่ได้
- `supported_cues_text()` ส่งรายการ cue ที่รองรับให้ Architect จริง แต่ **ปะปน 3 สิ่งที่ต่างกันในช่องเดียว**: (ก) Musical Direction ที่ AI อธิบายอิสระ (ข) Supported Cue ใน tag (ค) คำบรรยายดนตรีภาษาอังกฤษ (Free-form Caption)
- ในแชต Architect ถูกสั่งให้ใส่ทุกอย่างใน tag `[Section - cue, cue]` → ทุกคำที่ไม่อยู่ใน library (`CL.find_phrase`) ทำให้ `architect_problem` คืนข้อความ "not in the studio's cue library" แล้ว **ปฏิเสธทั้ง proposal**
- ตัวอย่างจริง: `[Verse 1 - bouncy bassline, rhythmic vocals]` — เป็นความคิดเชิงดนตรีที่ดี แต่ต้องไปอยู่ใน Music Caption ไม่ใช่ tag
- ทางที่ถูก: Supported Cue → Apply ได้; Free-form → แสดงเป็นคำแนะนำ/เสนอให้ส่งต่อ Generate (caption) ไม่ใช่ Apply และไม่ทำให้ส่วนที่ Apply ได้พัง
- ใน Analyze (report) ระบบดีกว่า: `op: set_cue` + `cues:` ตรวจต่อ cue, cue ที่ไม่รู้จักถูกบันทึกเป็น diagnostics (ไม่ทำให้ทั้งรายงานล้ม)

## 5. หลักการที่ควรเป็น: Conversation vs Action (ตาม brief ข้อ 5)
- **Conversation**: ตอบภาษาธรรมชาติ ไม่มี header/ marker ที่ผู้ใช้เห็น ไม่มี proposal เมื่อไม่ได้ขอ
- **Action**: เมื่อเจตนาคือ "แก้/เพิ่ม" จึงขอ structured (EDITS หรือ operation) ที่ **ผูกกับขอบเขตที่ระบุ** ผู้ใช้กด Apply เอง
- ใช้ session เดียวต่อ specialist ได้ต่อไป (ไม่ต้องแยก LLM): แยกที่ **ชั้นประกอบ prompt** และ **ชั้นแสดงผล/บันทึก** ไม่ใช่แยกโมเดล

## 6. แผน Minimal Patch (เรียงตามความสำคัญ รออนุมัติ)
| ลำดับ | แพตช์ | แก้ที่ | ขนาด | ลดความเสี่ยงกับ Apply/Structured | แก้ปัญหา |
|---|---|---|---|---|---|
| P1 | **แยกโน้ตระบบออกจากข้อความสนทนา**: เก็บโน้ตเป็นฟิลด์แยก (`ChatMessage.notes`) แสดงเป็นแถบเล็ก/ซ่อนได้ ไม่ต่อใน `text` ไม่ dump PROPOSAL ที่ถูกปฏิเสธ (เก็บใน trace); `chat_history` ใช้ `text` ที่สะอาด | `ask_ai`, `model.ChatMessage`, `bubble` | เล็ก-กลาง | ไม่แตะ parser | C1, ผู้ใช้เห็น "32 lines…" |
| P2 | **Architect ในแชตไม่ส่งทั้งเพลง**: ให้ใช้ *operations* เดียวกับ report (`add_section`, `set_cue`…) เป็นรูปแบบ Action (โปรแกรมเขียน tag เอง) และให้ตอบเป็นภาษาธรรมชาติเมื่อไม่ได้สั่งแก้ | `ARCHITECT_CHAT_RULE`, `ask_ai` (เรียก parser ops ที่มีอยู่แล้ว) | กลาง | ใช้โค้ดเดิม | กรณี intro ทั้งหมด, C2 (Architect) |
| P3 | **Validator แบบแยก cue**: Supported cue ที่ผ่านนำไป Apply; cue นอก library เป็น "คำแนะนำทิศทางดนตรี" แสดงเป็นข้อความ + ปุ่ม "คัดลอกเป็นส่วนของ caption" ไม่ปฏิเสธทั้งก้อน | `architect_problem`, `ask_ai` | กลาง | ลดการปฏิเสธ | C7, Cue System |
| P4 | **Intent/Scope**: ล้างหรือแสดงชัดเมื่อ selection ค้างจากคำสั่งก่อนหน้า (เช่น ล้าง selection หลัง Apply; ใส่ท้ายข้อความ "scope: ทั้งเพลง/การเลือก" เพียงเมื่อผู้ใช้ตั้งใจ); คำสั่งแบบ "เพิ่ม/แทรก section" ไม่ผูกกับ selection | `ask_ai`, `apply_chat_edit` | เล็ก | – | C3 |
| P5 | **ลด/ยุบ context**: ตัด `chat_field_reminder` ซ้ำ (เก็บแค่ตำแหน่งเดียว), ย่อ CHAT_CONTRACT (ตัวอย่าง LYRICS FORMAT ซ้ำใน `.md`), ไม่แนบ persona `.md` เต็มในเอนจินแถวตาราง (ใช้ role สั้น) | `spec_extra`, `chat_field_reminder`, `SE.prepare` | เล็ก-กลาง | ไม่แตะ `.md` ของ user | C4, C5, ความเร็ว (ดูรายงานความช้า) |
| P6 | **markup สี**: เลิกสอนในแชตของ Architect/Editor/Prosody หรือจำกัดเฉพาะ Advisor; เพิ่มตัวกรองวงเล็บปีกกา/LaTeX ท้ายข้อความก่อนบันทึก | `CHAT_CONTRACT`, `SE.plain_symbols` | เล็ก | – | C6 |
| P7 | **hand-off เฉพาะที่คุยได้**: ถ้าชี้ Music Director ให้บอก "ปุ่ม AI Recommend ที่ Generate" และไม่นับเป็นผู้รับแชต | `team_block`, `handoff_text` | เล็ก | – | C8 |
| P8 | **กฎการสนทนา (ไม่ใช่รายงาน)** ใน CHAT_CONTRACT: ตอบสั้นตรงคำถามก่อน, ถามกลับเฉพาะเมื่อขาดข้อมูลจำเป็น, ห้ามอ้างส่วนที่ไม่มี (EDITS) — **แก้ที่ contract ของโปรแกรม ไม่แตะ `.md`** | `llm_chat.CHAT_CONTRACT` | เล็ก | – | C2, C9, ความเป็นธรรมชาติ |
| P9 | Take Feedback: ยกเลิกโครงสร้าง 3 ข้อตายตัวเป็น "แนะนำ" และใช้ session เดียวกับ Advisor/Prosody | `take_feedback.SYSTEM_PROMPT` | เล็ก | – | C10 |

แนะนำให้ทำ P1 + P2 + P3 ก่อน (แก้กรณี intro และสิ่งที่ผู้ใช้เห็น) ตามด้วย P4–P6 (ลดบริบทและความเสียรูป) เพราะ P1–P3 ไม่ต้องรอเทสต์กับโมเดลจริงมาก ส่วน P5–P8 กระทบพฤติกรรมของ LLM ต้องลองกับ Gemma และ Grok ทั้งคู่

## 7. สิ่งที่ยังไม่ได้ตรวจ / ข้อจำกัดของ Audit
- ไม่ได้เรียก LLM จริง: พฤติกรรมของ prompt ที่แก้แล้วยังไม่ทราบ
- Grok: ในข้อมูลที่มีเห็นเฉพาะเวลาและขนาด (`est_in/est_out/sec`) ของ analyze/engine ไม่มีบันทึกคำตอบแชตจาก Grok เทียบกับ Gemma ในกรณีนี้ จึง **ไม่ยืนยันว่า Grok ตอบรูปแบบเสียแบบเดียวกัน** (เส้นทางโค้ดเป็นเส้นเดียวกัน)
- ไม่ได้ตรวจกรณีแชตของ Song Editor / Prosody ที่พังจริง (ไม่มีข้อมูลในประวัติ)
- ป้ายบอกขอบเขตการเลือกใน UI (`refresh_chat_ctx`) ยังไม่ได้ตรวจด้วยตาว่าผู้ใช้เห็นชัดแค่ไหนตอนส่งข้อความนั้น

## 8. สถานะหลังแก้ (2026-10-31)
- **P1 ทำแล้ว:** โน้ตระบบไม่อยู่ในข้อความสนทนาอีก (`ChatMessage.notes/hints/rejected`, `specialist_engine.handle_chat_answer`) ประวัติที่ส่งกลับให้โมเดลสะอาด
- **P2 ทำแล้ว:** Song Architect ในแชตใช้ OPERATIONS (โปรแกรมเขียน tag เอง) ไม่ส่งทั้งเพลง; เนื้อเพลงใส่เลขบรรทัด; PROPOSAL ของ Architect ไม่ถูกใช้
- **P3 ทำแล้ว:** cue ที่ไม่อยู่ใน library ไม่ทำให้ทั้งข้อเสนอพัง: ใช้เฉพาะ cue ที่รองรับ ส่วนที่เหลือเป็น "ทิศทางดนตรี" (ใช้ได้ทั้งในแชตและรายงาน Analyze)
- **เพิ่มตามที่ขอ:** สลับ Local ↔ Grok (หรือเปลี่ยนโมเดล) = session ใหม่ (ไม่ส่งประวัติ/summary เก่าให้โมเดลใหม่) และปุ่ม New session ในแชต
- ยังไม่ทำ: P4–P9
- **ทำเพิ่ม (รอบการสนทนาธรรมชาติ):** กฎสนทนากลาง `SE.CONVERSATION_RULES` + `STYLE_REMINDER` ท้ายคำถาม, แก้วลี "as fully as the question needs" ของ CHAT_CONTRACT ตอนประกอบ (`SE.chat_contract`), ตัด `chat_field_rule` ที่ซ้ำใน system, คำถาม/ขอบคุณ/ทักทายไม่ถูกแปลงเป็นการ์ด, ขอบคุณ/ทักทายตอบทันทีไม่เรียกโมเดล, ซ่อม marker ที่โมเดลลืม (เติมเฉพาะส่วนว่าง), Take Feedback เป็นร้อยแก้วแทนแม่แบบ 3 ข้อ
- **ยังเหลือ:** P4 (selection ค้าง), P6 บางส่วน, P7 (hand-off ไป Music Director), ลดขนาด prompt (P5 ส่วนที่เหลือ), Editor ยังตอบยาวบางคำถามกับ Gemma
