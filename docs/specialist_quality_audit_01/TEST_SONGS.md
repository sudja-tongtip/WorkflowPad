# TEST_SONGS

Three short Thai songs written for this audit (none is a real song of the user). The ID in front of every line is the **file line id the Song Critic receives** (`L012` = line 12 of the lyrics, tag lines count and get an id, blank lines are counted but show no id, so ids can skip a number; it is the id the model must cite and the parser checks). Style words and settings are what the request carried.

## S1 · Beautiful Words, Weak Song

**Designed to test:** Beautiful, ornate Thai imagery in every line, but nothing is at stake and nothing develops: the same feeling (a vague, lovely longing) is restated in new decoration. Tests whether the critic sees that pretty language is not a good song and can name where the idea repeats.

Style sent: `{'genre': 'Dream Pop', 'mood': 'Wistful', 'energy': 'Low', 'vocal': 'Female', 'language': 'Thai'}` · settings used for evidence: `{'bpm': 72, 'timesignature': '4', 'duration': 190}`

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

## S2 · Strong Story, Simple Lyrics

**Designed to test:** Plain everyday Thai, hardly any rhyme and no ornament, but clear characters, an event, a conflict and a turn (a child waits for a taxi-driver father who promised to come to sports day). Tests whether the critic values storytelling and does not mark the song down for plain words or sparse rhyme.

Style sent: `{'genre': 'Folk Pop', 'mood': 'Tender', 'energy': 'Medium', 'vocal': 'Male', 'language': 'Thai'}` · settings used for evidence: `{'bpm': 88, 'timesignature': '4', 'duration': 200}`

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

## S3 · Intentional Abstract / Atmospheric Song

**Designed to test:** Deliberately non-narrative: fragments of rain, a tin roof, blue light and a half-heard radio, held together by recurring motifs and one mood (the hush of an empty house at night). No events, no characters on purpose. Tests whether the critic understands the artistic intent and can tell a unified abstract piece from aimless writing.

Style sent: `{'genre': 'Ambient Indie', 'mood': 'Hushed', 'energy': 'Low', 'vocal': 'Female', 'language': 'Thai'}` · settings used for evidence: `{'bpm': 64, 'timesignature': '4', 'duration': 210}`

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
