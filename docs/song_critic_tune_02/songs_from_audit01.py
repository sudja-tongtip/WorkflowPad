# -*- coding: utf-8 -*-
"""The three test songs of the Song Critic quality audit 01. Written for this audit; none is a real song of the user."""

SONGS = {
    "S1": {
        "title": "S1 · Beautiful Words, Weak Song",
        "design": "Beautiful, ornate Thai imagery in every line, but nothing is at stake and nothing develops: the same feeling (a vague, lovely longing) is restated in new decoration. Tests whether the critic sees that pretty language is not a good song and can name where the idea repeats.",
        "style": {"genre": "Dream Pop", "mood": "Wistful", "energy": "Low", "vocal": "Female", "language": "Thai"},
        "settings": {"bpm": 72, "timesignature": "4", "duration": 190},
        "text": """[Verse 1]
แสงจันทร์ละเอียดอ่อนโรยบนผืนน้ำ
ลมพริ้วพรมกลีบฝันที่งดงาม
ดวงดาวพร่างพรายราวผ้าไหมอร่าม
ฉันเดินไปในความงามอันเงียบงัน

[Chorus]
โอ้ความคิดถึงล่องลอยในสายลม
เป็นความงามที่ละมุนนุ่มนวล
ใจฉันเอ่อล้นด้วยแสงสุกใส
เป็นความรู้สึกที่ไร้ชื่อเรียกขาน

[Verse 2]
เมฆขาวเอนกายพลิ้วไหวในฟ้า
ละอองรุ้งเริงร่าอยู่ปลายนิ้ว
ฉันเดินไปในความงามอันเงียบงัน
ท่ามกลางแสงสุกใสและเสียงลม

[Chorus]
โอ้ความคิดถึงล่องลอยในสายลม
เป็นความงามที่ละมุนนุ่มนวล
ใจฉันเอ่อล้นด้วยแสงสุกใส
เป็นความรู้สึกที่ไร้ชื่อเรียกขาน

[Bridge]
งดงามเหลือเกิน งดงามเหลือเกิน
ในความฝันที่ไม่มีวันจบสิ้น
แสงจันทร์ละเอียดอ่อนยังโรยรา
บนผืนน้ำแห่งความงามนิรันดร์

[Chorus]
โอ้ความคิดถึงล่องลอยในสายลม
เป็นความงามที่ละมุนนุ่มนวล
ใจฉันเอ่อล้นด้วยแสงสุกใส
เป็นความรู้สึกที่ไร้ชื่อเรียกขาน
""",
    },
    "S2": {
        "title": "S2 · Strong Story, Simple Lyrics",
        "design": "Plain everyday Thai, hardly any rhyme and no ornament, but clear characters, an event, a conflict and a turn (a child waits for a taxi-driver father who promised to come to sports day). Tests whether the critic values storytelling and does not mark the song down for plain words or sparse rhyme.",
        "style": {"genre": "Folk Pop", "mood": "Tender", "energy": "Medium", "vocal": "Male", "language": "Thai"},
        "settings": {"bpm": 88, "timesignature": "4", "duration": 200},
        "text": """[Verse 1]
พ่อสัญญาว่าวันกีฬาสีจะมา
ผมใส่เสื้อสีฟ้ายืนรอที่หน้าประตู
เพื่อนทุกคนมีพ่อแม่มานั่งดู
พ่อผมขับแท็กซี่ ติดอยู่ตรงไหนไม่รู้

[Pre-Chorus]
ครูเรียกชื่อผมลงวิ่งแล้ว
ผมหันไปมองถนนอีกที

[Chorus]
ผมวิ่งไปทั้งที่ไม่มีใครเชียร์
ได้ที่สุดท้ายแต่ผมวิ่งจนจบ
พอข้ามเส้นชัยผมก็เห็นพ่อ
ยืนหอบอยู่ข้างรั้ว เสื้อเปียกเหงื่อทั้งตัว

[Verse 2]
พ่อทิ้งรถไว้ริมถนนแล้ววิ่งมา
สองกิโลเมตร รองเท้าคู่เก่าหลุดข้างหนึ่ง
ผมโกรธที่พ่อมาช้าจนผมอยากร้องไห้
แต่พ่อพูดแค่ว่าพ่อขอโทษ พ่อมาแล้ว

[Pre-Chorus]
ผมไม่ได้พูดอะไรสักคำ
แค่ยื่นเหรียญให้พ่อ เหรียญที่ได้จากที่สุดท้าย

[Chorus]
ผมวิ่งไปทั้งที่ไม่มีใครเชียร์
ได้ที่สุดท้ายแต่พ่อเห็นผมวิ่งจนจบ
พ่อหัวเราะทั้งน้ำตา บอกว่าเหรียญนี้สวยที่สุด
ผมเลยลืมไปว่าผมโกรธ

[Outro]
วันนี้พ่อยังขับแท็กซี่เหมือนเดิม
แต่เหรียญนั้นแขวนอยู่หน้ารถทุกวัน
""",
    },
    "S3": {
        "title": "S3 · Intentional Abstract / Atmospheric Song",
        "design": "Deliberately non-narrative: fragments of rain, a tin roof, blue light and a half-heard radio, held together by recurring motifs and one mood (the hush of an empty house at night). No events, no characters on purpose. Tests whether the critic understands the artistic intent and can tell a unified abstract piece from aimless writing.",
        "style": {"genre": "Ambient Indie", "mood": "Hushed", "energy": "Low", "vocal": "Female", "language": "Thai"},
        "settings": {"bpm": 64, "timesignature": "4", "duration": 210},
        "text": """[Verse 1]
ฝนเคาะหลังคาสังกะสีเบาเบา
ไฟสีฟ้าค้างอยู่ที่ขอบประตู
วิทยุกระซิบเพลงที่ฟังไม่ชัด
ห้องนี้ยังจำเสียงเท้าได้

[Chorus]
เงียบ เงียบ เหมือนน้ำในแก้ว
เงียบ เงียบ เหมือนฝนบนหลังคา
แสงสีฟ้าไม่ไปไหน
วิทยุยังกระซิบอยู่

[Verse 2]
ม่านขยับทั้งที่ไม่มีลม
กาน้ำเย็นลงทีละหยด
เงาของเก้าอี้ยาวกว่าเมื่อวาน
ฝนยังเคาะอยู่ที่เดิม

[Chorus]
เงียบ เงียบ เหมือนน้ำในแก้ว
เงียบ เงียบ เหมือนฝนบนหลังคา
แสงสีฟ้าไม่ไปไหน
วิทยุยังกระซิบอยู่

[Bridge]
ไม่มีใครเปิดประตู
ไม่มีใครปิดไฟ
แค่ฝน แค่แสง แค่เสียงที่ไกลออกไป

[Outro]
ฝนเคาะหลังคาสังกะสีเบาเบา
แสงสีฟ้าค้างอยู่ที่ขอบประตู
""",
    },
}
