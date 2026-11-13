# METRICS (script output; no quality judgment)

| cell | arm | requests | latency s | in tok (est) | out tok (est) | in/out tok (reported) | answer chars | line ids (bad) | quoted Thai fragments (not found) |
|---|---|---|---|---|---|---|---|---|---|
| TestSong01_gemma | A one-shot | 1 | 12.4 | 2516 | 1551 | -/- | 3786 | 36 (0) | 22 (6) |
| TestSong01_gemma | B two-pass | 2 | 19.9 | 1100 | 2895 | -/- | 6861 | 23 (0) | 33 (16) |
| TestSong01_grok | A one-shot | 1 | 31.9 | 2516 | 834 | 2332/814 | 2030 | 16 (0) | 4 (0) |
| TestSong01_grok | B two-pass | 2 | 46.0 | 1100 | 1105 | 3249/1020 | 2477 | 0 (0) | 15 (4) |
| TestSong03_gemma | A one-shot | 1 | 14.0 | 2410 | 1706 | -/- | 4163 | 46 (6) | 12 (6) |
| TestSong03_gemma | B two-pass | 2 | 21.4 | 995 | 2850 | -/- | 6809 | 29 (0) | 30 (22) |
| TestSong03_grok | A one-shot | 1 | 38.9 | 2410 | 840 | 2196/777 | 2073 | 17 (0) | 3 (0) |
| TestSong03_grok | B two-pass | 2 | 27.0 | 995 | 1078 | 2881/967 | 2416 | 0 (0) | 8 (0) |

- TestSong01_gemma A quoted fragment not found in the lyrics: เฝ้ามอง
- TestSong01_gemma A quoted fragment not found in the lyrics: ดำดิ่ง
- TestSong01_gemma A quoted fragment not found in the lyrics: หยุดยืนมอง
- TestSong01_gemma A quoted fragment not found in the lyrics: สัมผัส
- TestSong01_gemma A quoted fragment not found in the lyrics: เป็นความรู้สึกดั่งสายหมอกยามเช้า
- TestSong01_gemma A quoted fragment not found in the lyrics: เป็นความรู้สึกที่ไม่มีชื่อในพจนานุกรม
- TestSong01_gemma B quoted fragment not found in the lyrics: ดำดิ่ง
- TestSong01_gemma B quoted fragment not found in the lyrics: จุดหักมุม
- TestSong01_gemma B quoted fragment not found in the lyrics: ตั้งคำถาม
- TestSong01_gemma B quoted fragment not found in the lyrics: การกระทำ
- TestSong01_gemma B quoted fragment not found in the lyrics: ฉันจะเก็บความงามนี้ไว้ในใจ
- TestSong01_gemma B quoted fragment not found in the lyrics: แม้ว่าแสงจันทร์จะเริ่มจาง
- TestSong01_gemma B quoted fragment not found in the lyrics: ล่องลอย สงบ และงดงามอย่างสมบูรณ์แบบ
- TestSong01_gemma B quoted fragment not found in the lyrics: จุดยึด
- TestSong01_gemma B quoted fragment not found in the lyrics: ความรู้สึกของการจมดิ่ง
- TestSong01_gemma B quoted fragment not found in the lyrics: คาดเดาได้
- TestSong01_gemma B quoted fragment not found in the lyrics: ความงามนี้ไม่มีวันเปลี่ยนแปลง
- TestSong01_gemma B quoted fragment not found in the lyrics: การยกระดับความรู้สึก
- TestSong01_gemma B quoted fragment not found in the lyrics: อย่าพยายามทำลายวงจรนี้ แต่จงเสริมสร้างมัน
- TestSong01_gemma B quoted fragment not found in the lyrics: วิ่งหนี
- TestSong01_gemma B quoted fragment not found in the lyrics: เพิ่มความเข้มข้น
- TestSong01_gemma B quoted fragment not found in the lyrics: พยายามจะหลุดลอย
- TestSong01_grok B quoted fragment not found in the lyrics: ความว่างเปล่า
- TestSong01_grok B quoted fragment not found in the lyrics: ความห่างไกล
- TestSong01_grok B quoted fragment not found in the lyrics: บรรยายความงาม
- TestSong01_grok B quoted fragment not found in the lyrics: ถ่ายทอดความรู้สึกคิดถึงที่ไร้ชื่อ
- TestSong03_gemma A line ids that do not exist: ['L6', 'L12', 'L18', 'L24', 'L29', 'L33']
- TestSong03_gemma A quoted fragment not found in the lyrics: บางสิ่งบางอย่างกำลังเกิดขึ้น
- TestSong03_gemma A quoted fragment not found in the lyrics: การตัดสินใจ
- TestSong03_gemma A quoted fragment not found in the lyrics: การครอบงำ
- TestSong03_gemma A quoted fragment not found in the lyrics: การดำรงอยู่
- TestSong03_gemma A quoted fragment not found in the lyrics: เพียง
- TestSong03_gemma A quoted fragment not found in the lyrics: วนกลับสู่จุดเดิม
- TestSong03_gemma B quoted fragment not found in the lyrics: ดำดิ่ง
- TestSong03_gemma B quoted fragment not found in the lyrics: หยุดนิ่ง
- TestSong03_gemma B quoted fragment not found in the lyrics: การรับรู้ถึงรายละเอียดเล็กๆ น้อยๆ
- TestSong03_gemma B quoted fragment not found in the lyrics: ยกระดับ
- TestSong03_gemma B quoted fragment not found in the lyrics: ความเงียบนี้ไม่ได้คงที่ แต่มันกำลังผ่านไป
- TestSong03_gemma B quoted fragment not found in the lyrics: ประตูยังคงรอใครสักคนเปิด
- TestSong03_gemma B quoted fragment not found in the lyrics: รอคอย
- TestSong03_gemma B quoted fragment not found in the lyrics: รู้สึก
- TestSong03_gemma B quoted fragment not found in the lyrics: บรรยายถึงความว่างเปล่าที่กำลังรอใครสักคน
- TestSong03_gemma B quoted fragment not found in the lyrics: ยืนยันว่าไม่มีใครทำอะไร
- TestSong03_gemma B quoted fragment not found in the lyrics: ภาพวาดเสียง
- TestSong03_gemma B quoted fragment not found in the lyrics: เคาะเบาเบา
- TestSong03_gemma B quoted fragment not found in the lyrics: ค่อยๆ เคลื่อนตัว
- TestSong03_gemma B quoted fragment not found in the lyrics: กลับสู่จุดเริ่มต้น
- TestSong03_gemma B quoted fragment not found in the lyrics: ความปรารถนา
- TestSong03_gemma B quoted fragment not found in the lyrics: การรอคอย
- TestSong03_gemma B quoted fragment not found in the lyrics: ยกระดับ
- TestSong03_gemma B quoted fragment not found in the lyrics: การแสดงออกทางอารมณ์
- TestSong03_gemma B quoted fragment not found in the lyrics: การยืนยันว่าไม่มีอะไรเกิดขึ้น
- TestSong03_gemma B quoted fragment not found in the lyrics: การรับรู้ถึงความว่างเปล่าที่กำลังรออะไรบางอย่าง
- TestSong03_gemma B quoted fragment not found in the lyrics: น้ำหนัก
- TestSong03_gemma B quoted fragment not found in the lyrics: เติมความปรารถนา
