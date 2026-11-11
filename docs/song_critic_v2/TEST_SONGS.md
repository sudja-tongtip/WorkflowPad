# TEST_SONGS (Song Critic V2)

Four short Thai songs written for these audits (none is a real song of the user). S1–S3 are the songs of Audit 01 (same lyrics and line ids); S4 is new and mixes strengths and weaknesses. **The model received only**: the neutral title (`Test Song 01…04`), the numbered lyrics, the STYLE line, the reply language and the program's evidence for the lyrics. No design note or description. The STYLE line is kept as in the earlier audits so the context is the same for both prompts (it can hint at a song's character; see KNOWN_LIMITATIONS).

Ids: `L012` = file line 12 (tag lines count; blank lines are counted but show no id).

## Test Song 01

Designed as: Beautiful, ornate Thai imagery in every line, but nothing is at stake and nothing develops: the same feeling (a vague, lovely longing) is restated in new decoration. Tests whether the critic sees that pretty language is not a good song and can name where the idea repeats.

STYLE sent: `{'genre': 'Dream Pop', 'mood': 'Wistful', 'energy': 'Low', 'vocal': 'Female', 'language': 'Thai'}`

```text
L001| [Verse 1]
L002| แสงจันทร์ละเอียดอ่อนโรยบนผืนน้ำ
L003| ลมพริ้วพรมกลีบฝันที่งดงาม
L004| ดวงดาวพร่างพรายราวผ้าไหมอร่าม
L005| ฉันเดินไปในความงามอันเงียบงัน
L007| [Chorus]
L008| โอ้ความคิดถึงล่องลอยในสายลม
L009| เป็นความงามที่ละมุนนุ่มนวล
L010| ใจฉันเอ่อล้นด้วยแสงสุกใส
L011| เป็นความรู้สึกที่ไร้ชื่อเรียกขาน
L013| [Verse 2]
L014| เมฆขาวเอนกายพลิ้วไหวในฟ้า
L015| ละอองรุ้งเริงร่าอยู่ปลายนิ้ว
L016| ฉันเดินไปในความงามอันเงียบงัน
L017| ท่ามกลางแสงสุกใสและเสียงลม
L019| [Chorus]
L020| โอ้ความคิดถึงล่องลอยในสายลม
L021| เป็นความงามที่ละมุนนุ่มนวล
L022| ใจฉันเอ่อล้นด้วยแสงสุกใส
L023| เป็นความรู้สึกที่ไร้ชื่อเรียกขาน
L025| [Bridge]
L026| งดงามเหลือเกิน งดงามเหลือเกิน
L027| ในความฝันที่ไม่มีวันจบสิ้น
L028| แสงจันทร์ละเอียดอ่อนยังโรยรา
L029| บนผืนน้ำแห่งความงามนิรันดร์
L031| [Chorus]
L032| โอ้ความคิดถึงล่องลอยในสายลม
L033| เป็นความงามที่ละมุนนุ่มนวล
L034| ใจฉันเอ่อล้นด้วยแสงสุกใส
L035| เป็นความรู้สึกที่ไร้ชื่อเรียกขาน
```

## Test Song 02

Designed as: Plain everyday Thai, hardly any rhyme and no ornament, but clear characters, an event, a conflict and a turn (a child waits for a taxi-driver father who promised to come to sports day). Tests whether the critic values storytelling and does not mark the song down for plain words or sparse rhyme.

STYLE sent: `{'genre': 'Folk Pop', 'mood': 'Tender', 'energy': 'Medium', 'vocal': 'Male', 'language': 'Thai'}`

```text
L001| [Verse 1]
L002| พ่อสัญญาว่าวันกีฬาสีจะมา
L003| ผมใส่เสื้อสีฟ้ายืนรอที่หน้าประตู
L004| เพื่อนทุกคนมีพ่อแม่มานั่งดู
L005| พ่อผมขับแท็กซี่ ติดอยู่ตรงไหนไม่รู้
L007| [Pre-Chorus]
L008| ครูเรียกชื่อผมลงวิ่งแล้ว
L009| ผมหันไปมองถนนอีกที
L011| [Chorus]
L012| ผมวิ่งไปทั้งที่ไม่มีใครเชียร์
L013| ได้ที่สุดท้ายแต่ผมวิ่งจนจบ
L014| พอข้ามเส้นชัยผมก็เห็นพ่อ
L015| ยืนหอบอยู่ข้างรั้ว เสื้อเปียกเหงื่อทั้งตัว
L017| [Verse 2]
L018| พ่อทิ้งรถไว้ริมถนนแล้ววิ่งมา
L019| สองกิโลเมตร รองเท้าคู่เก่าหลุดข้างหนึ่ง
L020| ผมโกรธที่พ่อมาช้าจนผมอยากร้องไห้
L021| แต่พ่อพูดแค่ว่าพ่อขอโทษ พ่อมาแล้ว
L023| [Pre-Chorus]
L024| ผมไม่ได้พูดอะไรสักคำ
L025| แค่ยื่นเหรียญให้พ่อ เหรียญที่ได้จากที่สุดท้าย
L027| [Chorus]
L028| ผมวิ่งไปทั้งที่ไม่มีใครเชียร์
L029| ได้ที่สุดท้ายแต่พ่อเห็นผมวิ่งจนจบ
L030| พ่อหัวเราะทั้งน้ำตา บอกว่าเหรียญนี้สวยที่สุด
L031| ผมเลยลืมไปว่าผมโกรธ
L033| [Outro]
L034| วันนี้พ่อยังขับแท็กซี่เหมือนเดิม
L035| แต่เหรียญนั้นแขวนอยู่หน้ารถทุกวัน
```

## Test Song 03

Designed as: Deliberately non-narrative: fragments of rain, a tin roof, blue light and a half-heard radio, held together by recurring motifs and one mood (the hush of an empty house at night). No events, no characters on purpose. Tests whether the critic understands the artistic intent and can tell a unified abstract piece from aimless writing.

STYLE sent: `{'genre': 'Ambient Indie', 'mood': 'Hushed', 'energy': 'Low', 'vocal': 'Female', 'language': 'Thai'}`

```text
L001| [Verse 1]
L002| ฝนเคาะหลังคาสังกะสีเบาเบา
L003| ไฟสีฟ้าค้างอยู่ที่ขอบประตู
L004| วิทยุกระซิบเพลงที่ฟังไม่ชัด
L005| ห้องนี้ยังจำเสียงเท้าได้
L007| [Chorus]
L008| เงียบ เงียบ เหมือนน้ำในแก้ว
L009| เงียบ เงียบ เหมือนฝนบนหลังคา
L010| แสงสีฟ้าไม่ไปไหน
L011| วิทยุยังกระซิบอยู่
L013| [Verse 2]
L014| ม่านขยับทั้งที่ไม่มีลม
L015| กาน้ำเย็นลงทีละหยด
L016| เงาของเก้าอี้ยาวกว่าเมื่อวาน
L017| ฝนยังเคาะอยู่ที่เดิม
L019| [Chorus]
L020| เงียบ เงียบ เหมือนน้ำในแก้ว
L021| เงียบ เงียบ เหมือนฝนบนหลังคา
L022| แสงสีฟ้าไม่ไปไหน
L023| วิทยุยังกระซิบอยู่
L025| [Bridge]
L026| ไม่มีใครเปิดประตู
L027| ไม่มีใครปิดไฟ
L028| แค่ฝน แค่แสง แค่เสียงที่ไกลออกไป
L030| [Outro]
L031| ฝนเคาะหลังคาสังกะสีเบาเบา
L032| แสงสีฟ้าค้างอยู่ที่ขอบประตู
```

## Test Song 04

Designed as: Mixed on purpose. STRONG: a specific opening (one house, one cracked cup, one truck), a short memorable hook, a closing image that pays off the cup. WEAK: the pre-chorus is stock philosophy; Verse 2 only names the feelings the opening already showed; the Bridge opens a new thread (a letter in a drawer) that is never used again; the last chorus is the first chorus unchanged after all that.

STYLE sent: `{'genre': 'Acoustic Pop', 'mood': 'Bittersweet', 'energy': 'Medium', 'vocal': 'Female', 'language': 'Thai'}`

```text
L001| [Verse 1]
L002| รถขนของจอดหน้าบ้านหลังเก่าตั้งแต่เช้า
L003| ยายยืนถือถ้วยสีฟ้าใบที่บิ่นตรงขอบ
L004| คนงานยกตู้ไม้ผ่านประตูที่ยายเคยปิดทุกคืน
L005| ฉันถือกุญแจไว้ ยังไม่กล้าส่งให้ใคร
L007| [Pre-Chorus]
L008| ชีวิตก็เป็นแบบนี้ ทุกอย่างต้องเปลี่ยนไป
L009| เวลาไม่เคยรอใคร มันเดินต่อไปเสมอ
L011| [Chorus]
L012| เก็บถ้วยใบเก่าไว้ เก็บไว้
L013| ไม่ใช่เพราะมันสวย แต่มันเป็นของยาย
L014| บ้านขายไปแล้วก็ช่าง
L015| แต่ถ้วยใบนี้ ฉันจะไม่ทิ้ง
L017| [Verse 2]
L018| ฉันเสียใจมาก ฉันคิดถึงยายเหลือเกิน
L019| ฉันรู้สึกเหงาและว้าเหว่ในใจ
L020| บ้านหลังนี้มีความทรงจำมากมาย
L021| ฉันไม่อยากให้ทุกอย่างหายไป
L023| [Bridge]
L024| ในลิ้นชักมีจดหมายซองเก่าใบหนึ่ง
L025| ลายมือยายเขียนไว้ว่าถึงหลานคนเล็ก
L026| ฉันยังไม่กล้าเปิดอ่าน
L028| [Chorus]
L029| เก็บถ้วยใบเก่าไว้ เก็บไว้
L030| ไม่ใช่เพราะมันสวย แต่มันเป็นของยาย
L031| บ้านขายไปแล้วก็ช่าง
L032| แต่ถ้วยใบนี้ ฉันจะไม่ทิ้ง
L034| [Outro]
L035| น้ำชาเย็นแล้วในถ้วยสีฟ้า
L036| รอยบิ่นยังอยู่ตรงที่ยายเคยจับ
```
