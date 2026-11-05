# แก้ Cover / Repaint ใช้ไม่ได้ (Kaola ACE-Step + flash_attn)

## อาการ
กด Cover หรือ Repaint แล้ว ComfyUI ตอบ error:

```
ACE_STEP_Repaint: Failed to initialize ACE-Step handlers: No package metadata was found for flash_attn
```

(ใน `comfyui.log` จะเห็นบรรทัด `Using attention: flash_attention_2` ตามด้วย `Failed with flash_attention_2`)

## สาเหตุ
- โหนดอื่นใน ComfyUI (ที่พบคือ `seedvr2_videoupscaler`) สร้างโมดูล `flash_attn` ปลอมไว้ตอนเริ่มทำงาน
- โหนด Kaola ตรวจแค่ว่า `import flash_attn` ได้หรือไม่ จึงเลือกโหมด `flash_attention_2`
- transformers ไปหา package ตัวจริงไม่เจอ จึงพัง
- Generate ปกติไม่โดน เพราะไม่ใช้โหนด Kaola

## วิธีแก้ A: แก้ไฟล์ Kaola (ถาวร ไม่เสีย SeedVR2)

ไฟล์อยู่ในโฟลเดอร์โหนด Kaola ของ ComfyUI:
`<ComfyUI>\custom_nodes\ComfyUI-kaola-ace_step\`

1. ปิด ComfyUI
2. สำรองไฟล์ก่อนแก้ (คัดลอกเก็บไว้ข้างๆ)
3. เปิด `acestep_wrapper.py` หาฟังก์ชัน `_get_attention_implementation` แล้วแก้เป็น:

```python
    def _get_attention_implementation(self) -> str:
        """Determine best attention implementation"""
        try:
            import flash_attn
            # Another node may leave a fake flash_attn module in sys.modules.
            # Only trust a flash_attn that is really installed.
            import importlib.metadata as _md
            _md.version("flash_attn")
            return "flash_attention_2"
        except Exception:
            return "sdpa"
```

4. (ไม่บังคับ แต่แนะนำ) เปิด `acestep_repo\acestep\core\generation\handler\init_service.py` หาฟังก์ชัน `is_flash_attention_available` ตอนท้ายแก้เป็น:

```python
        try:
            import flash_attn
            import importlib.metadata as _md
            _md.version("flash_attn")
            return True
        except Exception:
            return False
```

5. เปิด ComfyUI ใหม่ แล้วลอง Repaint

**ผลที่ควรเห็น:** ใน `comfyui.log` ขึ้น `Using attention: sdpa` และงานทำจนเสร็จ ไม่มี error flash_attn

**หมายเหตุ:** ถ้าอัปเดตโหนด Kaola การแก้ไขอาจถูกเขียนทับ ต้องแก้ซ้ำ

## วิธีแก้ B: ปิดโหนดที่สร้าง flash_attn ปลอม (ไม่ต้องแก้โค้ด)

1. ปิด ComfyUI
2. เปลี่ยนชื่อโฟลเดอร์ `custom_nodes\seedvr2_videoupscaler` เป็น `seedvr2_videoupscaler.disabled` (หรือสั่ง Disable ใน ComfyUI Manager)
3. เปิด ComfyUI ใหม่

ข้อเสีย: ใช้ SeedVR2 อัปสเกลวิดีโอไม่ได้ระหว่างที่ปิด

## ตรวจว่าแก้สำเร็จ
- ดู `<ComfyUI>\user\comfyui.log` หาบรรทัด `Using attention:` ต้องเป็น `sdpa`
- ใน Music Studio ลอง Repaint ช่วงสั้นๆ (5 วินาที) ก่อน

## ถ้ายังไม่หาย
- ค้นหา `flash_attn` ใน `comfyui.log` แล้วดูว่า error มาจากไฟล์ไหน อาจมีโหนดอื่นสร้าง stub เพิ่ม
- ค้นหาในโฟลเดอร์ `custom_nodes` ว่ามีโหนดไหนมี `sys.modules['flash_attn']`
