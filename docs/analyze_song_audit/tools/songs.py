# -*- coding: utf-8 -*-
"""Eight original test songs (written for this audit, no copyright issue) and their human-checkable reference answers.
anchors      = {line text: spoken syllables} counted by hand (simple words, unambiguous counts)
rhyme_true   = pairs of line texts whose last syllables rhyme exactly by design
rhyme_false  = pairs of adjacent lines that do NOT rhyme (a report claiming they rhyme is a factual error)
expect       = what a good report should (or must not) say; used by the deterministic checks and by the human reading
"""

SONGS = {}

SONGS["S1"] = dict(
    title="ฝนหน้าบ้าน", kind="Thai, clear rhyme and structure", lang="th", genre="Ballad", mood="Romantic", bpm=76, timesig="4", duration=180, vocal="female vocal", energy="low energy",
    text="""[Verse 1]
ฝนพรำเปียกหน้าบ้าน
เธอยืนอยู่ตรงนั้นนาน
ฉันถือร่มคันเก่า
เธอเดินมาเบาเบา

[Pre-Chorus]
ไม่ต้องพูดอะไรสักคำ
แค่ยืนใกล้ก็รู้ทั้งหมด

[Chorus]
ให้ฝนเป็นพยาน ว่าฉันรักเธอ
ให้ฝนเป็นพยาน ว่าเธอรักฉัน
ถ้าวันนี้ฟ้าไม่ยอมหยุดร้องไห้
ฉันจะอยู่กับเธอไปจนฟ้าใส

[Verse 2]
เสื้อเปียกทั้งตัวก็ไม่เป็นไร
มือของเธออุ่นกว่าที่ฉันคิด
ฝนจะตกอีกกี่วันก็ช่าง
ขอแค่เธอยังยืนอยู่ตรงนี้

[Chorus]
ให้ฝนเป็นพยาน ว่าฉันรักเธอ
ให้ฝนเป็นพยาน ว่าเธอรักฉัน
ถ้าวันนี้ฟ้าไม่ยอมหยุดร้องไห้
ฉันจะอยู่กับเธอไปจนฟ้าใส

[Bridge]
บางเรื่องไม่ต้องอธิบาย
แค่ไม่ปล่อยมือกันก็พอ
""",
    anchors={"ฝนพรำเปียกหน้าบ้าน": 5, "เธอยืนอยู่ตรงนั้นนาน": 6, "ฉันถือร่มคันเก่า": 5, "เธอเดินมาเบาเบา": 5, "ไม่ต้องพูดอะไรสักคำ": 7, "ให้ฝนเป็นพยาน ว่าฉันรักเธอ": 9, "ฉันจะอยู่กับเธอไปจนฟ้าใส": 9, "บางเรื่องไม่ต้องอธิบาย": 7},
    rhyme_true=[("ฝนพรำเปียกหน้าบ้าน", "เธอยืนอยู่ตรงนั้นนาน"), ("ฉันถือร่มคันเก่า", "เธอเดินมาเบาเบา"), ("ถ้าวันนี้ฟ้าไม่ยอมหยุดร้องไห้", "ฉันจะอยู่กับเธอไปจนฟ้าใส")],
    rhyme_false=[("ไม่ต้องพูดอะไรสักคำ", "แค่ยืนใกล้ก็รู้ทั้งหมด"), ("ให้ฝนเป็นพยาน ว่าฉันรักเธอ", "ให้ฝนเป็นพยาน ว่าเธอรักฉัน"), ("เสื้อเปียกทั้งตัวก็ไม่เป็นไร", "มือของเธออุ่นกว่าที่ฉันคิด"), ("บางเรื่องไม่ต้องอธิบาย", "แค่ไม่ปล่อยมือกันก็พอ")],
    expect=dict(sections=["Verse 1", "Pre-Chorus", "Chorus", "Verse 2", "Chorus", "Bridge"], good_hook=True, summary="A sound, coherent ballad: the chorus is memorable and the story is steady. Few or no required changes."),
)

SONGS["S2"] = dict(
    title="เสียงในเมือง", kind="Thai, rhyme deliberately irregular", lang="th", genre="Folk", mood="Calm", bpm=88, timesig="4", duration=170, vocal="male vocal", energy="low energy",
    text="""[Verse 1]
เช้านี้ฉันตื่นมาพร้อมกับเสียงรถเมล์
ป้ายหยุดหน้าตึกยังเหมือนเดิมทุกวัน
ลุงขายกาแฟยิ้มให้ฉันเหมือนเคย
ฉันซื้อแก้วเดิมไม่ต้องบอกอะไร

[Chorus]
เมืองนี้ไม่เคยเงียบ แต่ฉันได้ยินเสียงตัวเอง
ในเสียงที่ดังที่สุด
เมืองนี้ไม่เคยเงียบ แต่ฉันได้ยินเสียงตัวเอง

[Verse 2]
เพื่อนร่วมงานถามว่าสุขไหม
ฉันตอบว่าก็ดีตามปกติ
แต่ระหว่างบรรทัดที่พิมพ์ในแชต
มีประโยคที่ฉันลบไปหลายรอบ

[Chorus]
เมืองนี้ไม่เคยเงียบ แต่ฉันได้ยินเสียงตัวเอง
ในเสียงที่ดังที่สุด
เมืองนี้ไม่เคยเงียบ แต่ฉันได้ยินเสียงตัวเอง
""",
    anchors={"ลุงขายกาแฟยิ้มให้ฉันเหมือนเคย": 9, "ฉันซื้อแก้วเดิมไม่ต้องบอกอะไร": 9, "เพื่อนร่วมงานถามว่าสุขไหม": 7, "ฉันตอบว่าก็ดีตามปกติ": 9},
    rhyme_true=[],
    rhyme_false=[("ลุงขายกาแฟยิ้มให้ฉันเหมือนเคย", "ฉันซื้อแก้วเดิมไม่ต้องบอกอะไร"), ("เพื่อนร่วมงานถามว่าสุขไหม", "ฉันตอบว่าก็ดีตามปกติ"), ("แต่ระหว่างบรรทัดที่พิมพ์ในแชต", "มีประโยคที่ฉันลบไปหลายรอบ")],
    expect=dict(sections=["Verse 1", "Chorus", "Verse 2", "Chorus"], no_rhyme_demand=True, summary="Rhyme is absent by design. A good report does not call the missing rhyme a fault; the repeated chorus line is an identity, not a flaw."),
)

SONGS["S3"] = dict(
    title="ร้านซักรีด", kind="Thai, weak hook but good verses", lang="th", genre="Pop", mood="Nostalgic", bpm=84, timesig="4", duration=190, vocal="female vocal", energy="medium energy",
    text="""[Verse 1]
ร้านซักรีดหัวมุมถนนปิดไปตั้งแต่ปีก่อน
ตู้ไปรษณีย์สีแดงยังเปื้อนสีเทาของควัน
ฉันยังจำกลิ่นผงซักฟอกบนแขนเสื้อที่เธอยืม
ตอนฝนตกเราวิ่งหนีเข้าไปในร้านนั้น

[Chorus]
ฉันคิดถึงเธอ คิดถึงเธอ
ฉันรักเธอ ไม่เปลี่ยนไป
คิดถึงเธอ คิดถึงเธอ
ฉันรักเธอ ตลอดไป

[Verse 2]
ป้ายราคาที่เธอเคยแอบเปลี่ยนให้ฉันยังติดอยู่
ลูกกุญแจดอกเล็กในลิ้นชักที่ไม่เคยมีใครเปิด
ฉันยืนอยู่หน้าบานเหล็กที่ลงสนิมจนเกือบกลืนชื่อร้าน
แล้วหัวเราะกับตัวเองว่าฉันยังเอาเสื้อตัวนั้นไปไม่ได้คืน

[Chorus]
ฉันคิดถึงเธอ คิดถึงเธอ
ฉันรักเธอ ไม่เปลี่ยนไป
คิดถึงเธอ คิดถึงเธอ
ฉันรักเธอ ตลอดไป
""",
    anchors={"คิดถึงเธอ คิดถึงเธอ": 6, "ฉันรักเธอ ไม่เปลี่ยนไป": 6, "ฉันรักเธอ ตลอดไป": 6, "ตู้ไปรษณีย์สีแดงยังเปื้อนสีเทาของควัน": 13},
    rhyme_true=[],
    rhyme_false=[("ร้านซักรีดหัวมุมถนนปิดไปตั้งแต่ปีก่อน", "ตู้ไปรษณีย์สีแดงยังเปื้อนสีเทาของควัน")],
    expect=dict(sections=["Verse 1", "Chorus", "Verse 2", "Chorus"], weak_hook=True, summary="The verses are specific and strong; the chorus is generic (no image, repeats the same words, `ไป` rhymes with itself). A good report says the hook is the weak part and the verses are not."),
)

SONGS["S4"] = dict(
    title="เต้นต่อไป", kind="Thai, strong hook, nothing needs fixing", lang="th", genre="Pop Rock", mood="Energetic", bpm=124, timesig="4", duration=185, vocal="male vocal", energy="high energy",
    text="""[Verse 1]
รองเท้าคู่เก่าพื้นบางจนรู้สึกถึงทราย
กระเป๋าเบากว่าเมื่อวานก็ไม่เป็นไร
ฉันเดินผ่านร้านเพลงแล้วเสียงเบสมันเรียกชื่อ
เหมือนมันรู้ว่าวันนี้ฉันต้องการอะไร

[Chorus]
ลุกขึ้นมาเต้น ลุกขึ้นมาเต้น
ต่อให้พื้นร้าวก็ยังเต้นได้
ลุกขึ้นมาเต้น ลุกขึ้นมาเต้น
เสียงเบสจะพาฉันไปให้ไกล

[Verse 2]
เจ้านายโทรมาตอนตีหนึ่งว่าพรุ่งนี้ต้องเร่ง
ฉันกดปิดเสียงแล้วเปิดลำโพงตัวเก่า
ข้างห้องเคาะผนังแต่ฉันยิ้มให้ผนังนั้น
เพราะเขาก็คงฟังเพลงนี้อยู่เหมือนกัน

[Chorus]
ลุกขึ้นมาเต้น ลุกขึ้นมาเต้น
ต่อให้พื้นร้าวก็ยังเต้นได้
ลุกขึ้นมาเต้น ลุกขึ้นมาเต้น
เสียงเบสจะพาฉันไปให้ไกล
""",
    anchors={"ลุกขึ้นมาเต้น ลุกขึ้นมาเต้น": 8, "ต่อให้พื้นร้าวก็ยังเต้นได้": 8, "เสียงเบสจะพาฉันไปให้ไกล": 8, "เหมือนมันรู้ว่าวันนี้ฉันต้องการอะไร": 11},
    rhyme_true=[("กระเป๋าเบากว่าเมื่อวานก็ไม่เป็นไร", "เหมือนมันรู้ว่าวันนี้ฉันต้องการอะไร"), ("ต่อให้พื้นร้าวก็ยังเต้นได้", "เสียงเบสจะพาฉันไปให้ไกล")],
    rhyme_false=[("ลุกขึ้นมาเต้น ลุกขึ้นมาเต้น", "ต่อให้พื้นร้าวก็ยังเต้นได้")],
    expect=dict(sections=["Verse 1", "Chorus", "Verse 2", "Chorus"], good_hook=True, summary="A strong, repeatable hook with a concrete image. A good report says so and does not ask to change the hook."),
)

SONGS["S5"] = dict(
    title="ชานชาลา", kind="Thai, contradictory story and viewpoint", lang="th", genre="Indie Pop", mood="Melancholic", bpm=92, timesig="4", duration=175, vocal="female vocal", energy="medium energy",
    text="""[Verse 1]
ฉันขึ้นรถไฟเที่ยวสุดท้ายออกมาคนเดียว
ทิ้งเธอไว้ที่ชานชาลาโดยไม่หันกลับไปมอง
เมื่อวานนี้ฉันบอกลาเธอไปแล้วทุกอย่าง
ค่ำนี้ฉันเลยได้นอนหลับเต็มตาเป็นครั้งแรก

[Chorus]
ฉันไม่เคยรอใครเลยในชีวิตนี้
แต่ฉันรอเธออยู่ที่สถานีทุกเช้า
ฉันไม่เคยรอใครเลยในชีวิตนี้
เธอไม่เคยมาแต่ฉันก็ยังรอเธอ

[Verse 2]
เธอทิ้งฉันไว้ที่ชานชาลาแล้วเดินหายไปในฝน
ฉันยืนมองรถไฟคันนั้นจนไฟท้ายเหลือเพียงจุดเดียว
พรุ่งนี้ฉันจะกลับมาที่นี่ตามที่สัญญากับเธอไว้
เพราะฉันไม่เคยไปจากเมืองนี้เลยสักวัน
""",
    anchors={"ฉันไม่เคยรอใครเลยในชีวิตนี้": 10},
    rhyme_true=[],
    rhyme_false=[],
    expect=dict(sections=["Verse 1", "Chorus", "Verse 2"], contradiction=True, summary="Who leaves and who waits flip between Verse 1 (the narrator leaves on the last train, calls it goodbye) and Verse 2 (the other person leaves, the narrator promises to return, never left the city); the chorus both denies and claims waiting. A good report names this contradiction with line evidence."),
)

SONGS["S6"] = dict(
    title="ตลาดเช้า", kind="Thai, very dense lines for a short duration", lang="th", genre="Hip Hop", mood="Energetic", bpm=70, timesig="4", duration=120, vocal="male vocal", energy="medium energy",
    text="""[Verse 1]
ฉันเดินผ่านตลาดเช้าตรู่ที่คนเริ่มขายของริมถนนสายเล็กหลังโรงเรียนเก่า
แม่ค้าตะโกนเรียกลูกค้าแข่งกับเสียงมอเตอร์ไซค์ที่ขับผ่านไปมาทุกนาทีไม่เว้นแม้แต่วันหยุด
ฉันจดรายการที่แม่ฝากซื้อไว้ในมือถือแต่สุดท้ายก็ซื้อของที่ไม่ได้อยู่ในรายการมากกว่าครึ่ง
ป้าขายผักยัดถุงเพิ่มให้อีกสองกำเพราะเห็นว่าฉันผอมลงไปมากตั้งแต่ครั้งสุดท้ายที่แวะมา

[Chorus]
ตลาดตอนเช้าคือเมืองที่ไม่เคยหลับและไม่เคยถามว่าใครเหนื่อยหรือใครเพิ่งเสียใจมาจากไหน
ฉันเดินไปตามเสียงเรียกของคนที่จำชื่อฉันไม่ได้แต่จำได้ว่าฉันชอบกินอะไรตอนเช้าทุกวัน
ตลาดตอนเช้าคือเมืองที่ไม่เคยหลับและไม่เคยถามว่าใครเหนื่อยหรือใครเพิ่งเสียใจมาจากไหน
ฉันเดินไปตามเสียงเรียกของคนที่จำชื่อฉันไม่ได้แต่จำได้ว่าฉันชอบกินอะไรตอนเช้าทุกวัน

[Verse 2]
ลุงขายปลาเล่าเรื่องเมื่อสามสิบปีก่อนที่ตลาดนี้ยังเป็นแค่ทุ่งนากับคลองเล็กๆที่เด็กๆลงไปว่ายน้ำกัน
ฉันฟังจนปลาในถังเริ่มหยุดดิ้นแล้วลุงก็หัวเราะแล้วบอกว่าวันนี้ลดให้เพราะฉันเป็นลูกค้าที่ฟังเก่ง
""",
    anchors={"ฉันเดินผ่านตลาดเช้าตรู่ที่คนเริ่มขายของริมถนนสายเล็กหลังโรงเรียนเก่า": 21},
    rhyme_true=[],
    rhyme_false=[],
    expect=dict(sections=["Verse 1", "Chorus", "Verse 2"], dense=True, summary="Lines run about 20-30 syllables each at 70 BPM for 120 s: far denser than the 6-10 syllable guide. A good report names the density risk for the duration without claiming it can predict the sound."),
)

SONGS["S7"] = dict(
    title="Keys In Your Car", kind="English", lang="en", genre="Indie Pop", mood="Hopeful", bpm=110, timesig="4", duration=180, vocal="female vocal", energy="medium energy",
    text="""[Verse 1]
I left my keys inside your car
Now every street is twice as far
You said you'd meet me by the bridge
But all I found was a broken fridge

[Chorus]
So call me when the lights go down
I'll be the one still in this town
So call me when the lights go down
I'll be the one still in this town

[Verse 2]
The corner shop remembers me
They keep my seat beside the tea
I tell the clerk about your phone
And how it rings when I'm alone

[Chorus]
So call me when the lights go down
I'll be the one still in this town
So call me when the lights go down
I'll be the one still in this town
""",
    anchors={"I left my keys inside your car": 8, "So call me when the lights go down": 8, "I'll be the one still in this town": 8},
    rhyme_true=[("I left my keys inside your car", "Now every street is twice as far"), ("So call me when the lights go down", "I'll be the one still in this town"), ("The corner shop remembers me", "They keep my seat beside the tea"), ("I tell the clerk about your phone", "And how it rings when I'm alone")],
    rhyme_false=[("You said you'd meet me by the bridge", "But all I found was a broken fridge")],
    expect=dict(sections=["Verse 1", "Chorus", "Verse 2", "Chorus"], english=True, summary="Clean English pop with couplet rhyme. `bridge/fridge` DO rhyme (exact); the reference lists only the sure pairs, so the check does not penalise a report that finds it. A report must not use Thai rhyme rules."),
)

SONGS["S8"] = dict(
    title="ไฟถนน", kind="Thai with section/cue tags, backing vocals and parentheses", lang="th", genre="Soul", mood="Dreamy", bpm=68, timesig="4", duration=200, vocal="female vocal", energy="low energy",
    text="""[Intro - soft vocals]
(ooh... ooh...)

[Verse 1 - breathy vocals]
เมืองนี้เงียบหลังเที่ยงคืน (เงียบมาก)
ไฟถนนกระพริบเหมือนมันกำลังหายใจ
ฉันเดินคนเดียวใต้ฟ้าสีส้ม (ฟ้าสีส้ม)
ถึงยังไงก็ไม่มีใครรอ

[Chorus - powerful belting, harmonies]
ส่องมาที่ฉัน (ส่องมา)
ให้ฉันเห็นทาง (เห็นทาง)
ถ้าทั้งเมืองหลับไปแล้ว
ฉันจะเป็นคนเดียวที่ตื่น (ตื่นอยู่)

[Bridge - spoken word]
ไฟดวงสุดท้ายดับลงตอนตีสี่
แต่ฟ้าทางตะวันออกเริ่มเปลี่ยนสี

[Outro - fade out]
(la la la)
""",
    anchors={"ไฟถนนกระพริบเหมือนมันกำลังหายใจ": 11},
    rhyme_true=[],
    rhyme_false=[],
    expect=dict(sections=["Intro - soft vocals", "Verse 1 - breathy vocals", "Chorus - powerful belting, harmonies", "Bridge - spoken word", "Outro - fade out"], tags=True, summary="Section tags are structure and cues, not lyrics; text in parentheses is backing vocal. A Songwriter report must not propose to rewrite the cue tags (the Music Producer's work) and must not count tags as sung lines."),
)
