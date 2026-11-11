# QUALITY_COMPARISON — baseline vs candidate, per song and backend

Format: **Fact** = what the raw answer says (file in `RAW_RESPONSES/`), **Reading** = the auditor's interpretation. Run files: `TestSong0N_<gemma|grok>_<baseline|candidate>_response.txt`.

## Test Song 01 (the one with a weakness to name)

### Gemma
| | Baseline | Candidate |
|---|---|---|
| Fact: verdict | `reading`: "…การเดินทางทางอารมณ์ค่อนข้างวนเวียนอยู่กับความงามเดิมๆ โดยไม่มีการพัฒนา…" | `headline`: "…ประสบความสำเร็จในการสร้างบรรยากาศที่ชวนฝันได้อย่างดีเยี่ยม" |
| Fact: findings | 3 concern (2 high): L002-L004 "บอก มากกว่า แสดง"; the line at L005/L016 (ranges `L005-L016`); "ไร้ชื่อเรียกขาน" (`L011-L023`). 1 strength, `priority: high`. | 2 keep (opening, Bridge), 1 strength (the line at L005/L016, *dropped by the parser: `L005, L016`*), 1 concern medium (L017 not tied to the longing). |
| Fact: recommendations | `"ยืนมอง" / "สัมผัส"`, `"เป็นความรู้สึกที่เหมือนรอใครสักคน"` (replacement wording); `keep: -` throughout. | `"พัดพาความคิดถึงมา"` / `"ลมที่ร่ำร้องถึงใครบางคน"` (replacement wording); `keep:` states a real cost ("…จะสูญเสียโอกาสในการผลักดันความรู้สึกให้ลึกขึ้น"). |
| Reading | Sees the stasis and the repetition; also overrates the hook ("น่าจดจำมาก", high strength) and judges craft ("telling vs showing"); every fix is a lyric. | Misses the stasis; the repeated line becomes a strength. A single real point (the Verse 2 detail is not tied to the chorus's longing) is stated with a trade-off but fixed with a lyric. |
| Direction | **Candidate worse on the core judgment, better on trade-offs and format.** |

### Grok
| | Baseline | Candidate |
|---|---|---|
| Fact: verdict | `headline`: "…แต่ยังคงเป็นบรรยากาศเดียวตลอดทั้งเพลง" | `headline`: "…ทำได้ดีในฐานะชิ้นบรรยากาศที่ไม่ต้องการการพัฒนาเพิ่ม" |
| Fact: findings | concern high (the line at L5/L16, *dropped by the parser: `L5,L16`*); strength (chorus). | keep (opening), keep (chorus): "การซ้ำเป็นอุปกรณ์ที่จงใจใช้เพื่อรักษาบรรยากาศ จึงควรคงไว้". No concern. |
| Fact: recommendation | "ลองเพิ่มรายละเอียดเล็กน้อยที่เปลี่ยนไปในเวิร์สที่สองเพื่อให้ภาพพัฒนา" | none |
| Reading | Names the problem, with a generic action (what: small detail; where: Verse 2; why: so the picture develops). | Declares the repetition deliberate although nothing in the lyrics says so; the weakness is not reported. **Direction: worse** (Judgment, Understanding), restraint over-applied. |

## Test Song 02 (strong story, plain words)

### Gemma
| | Baseline | Candidate |
|---|---|---|
| Fact: findings | strength L014-L015 (`high`), **concern L020** ("ผมโกรธที่พ่อมาช้า…" is "direct statement" → `"ผมโกรธจนน้ำตาแทบจะไหล"`), strength L030 (`medium`), **concern L024** ("ผมไม่ได้พูดอะไรสักคำ" is too short → add `"เพียงแต่ยืนนิ่ง"`). | strength L14-L15, strength L028-L030 ("เพิ่ม 'แต่พ่อเห็นผมวิ่งจนจบ'… ยกระดับความรู้สึก"), keep L034-L035. No concern. |
| Reading | Values the story, then edits the two quietest, best lines; English tags in Thai prose ("(concrete image)", "(direct statement)"). | Reads the story correctly, notices the second chorus's added line, leaves everything. **Direction: better** (Judgment, Restraint, Scope, Consistency). Nothing to act on, so Actionability is n/a. |

### Grok
| | Baseline | Candidate |
|---|---|---|
| Fact: findings | strength L14-L15; **concern L34-L35**: the Outro "เพียงสรุปสถานการณ์เดิมโดยไม่เพิ่มรายละเอียดใหม่" → "ลองเพิ่มรายละเอียดเล็กน้อย…". (Same reading as Audit 01.) | strength L28-L31 ("Chorus ที่สองเพิ่มรายละเอียดใหม่"), keep L12, L28 (the repeated line; *dropped by the parser: `L12, L28`*). No concern. |
| Reading | The Outro's new image (the medal on the dashboard) is called "no new detail". | No misreading; evidence correct; a keep finding is lost to the parser. **Direction: better**, one Evidence mark lost to the format. |

## Test Song 03 (atmospheric)

### Gemma
| | Baseline | Candidate |
|---|---|---|
| Fact: findings | strength L002-L003 (`high`), strength L008-L010 (`high`), concern L014-L016 (the movements are "ปรากฏการณ์ธรรมชาติ… ลองเพิ่มการกระทำที่เกิดจากเจตนา… `มือเอื้อมไปแตะม่าน`"), concern L028 (`แค่ฝนที่ริน` / `แค่แสงที่เลือน`). | keep L008-L009, concern medium L017 ("ฝนยังเคาะอยู่ที่เดิม" returns too fast → `"ฝนยังเคาะ แต่หนักกว่าเมื่อวาน"`; cost stated in `keep`), keep L028 ("แค่" … "เรียบง่ายแต่หนักแน่น"). |
| Reading | Two edits that push a still song toward intention and decoration, each as a lyric. | The `แค่` concern is gone, but `reading` says the song lacks "การ 'ขยับ' ทางอารมณ์ที่ชัดเจนในท่อนกลาง", flags the motif, and the NOTE reads a waiting-for-someone story into the lyrics. **Direction: mixed** (Restraint, Trade-offs better; Understanding worse). |

### Grok
| | Baseline | Candidate |
|---|---|---|
| Fact: findings | concern **high** L20-L23: the second chorus repeats word for word → "ลองเพิ่มรายละเอียดเล็กน้อย…"; strength L2-L5. | keep L008-L011 ("การซ้ำนี้รักษาบรรยากาศ… ควรคงไว้"), strength L002-L005. |
| Reading | A `high` concern against a repetition the verdict itself calls the song's method ("ใช้ภาพซ้ำเพื่อคงอารมณ์เดิม"). | Verdict: "เป็นเพลงที่เน้นอารมณ์นิ่งมากกว่าการเล่าเรื่อง"; chorus kept. **Direction: better.** |

## Two-line summary
- Candidate is better where the baseline invented problems (02 both, 03 Grok) and on format/trade-offs (Gemma `priority`, real costs in `keep`).
- Candidate is worse where the song really has a weakness (01 both): it reports success and keeps the repetition. Actionability could not be shown improved: with so few concerns the candidate gave 2 recommendations in 6 runs (both with lyric wording).
