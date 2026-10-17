"""รันเทสต์ทุกไฟล์ในชุดนี้ทีเดียว:  python -m story_generator.tests

แต่ละไฟล์เป็นสคริปต์ที่พิมพ์ PASS/FAIL ของตัวเองแล้วปิดท้ายด้วย ALL PASS — ตัวนี้แค่รันทีละไฟล์
ในโปรเซสแยก (กันไฟล์ที่ monkeypatch โมดูล production ไปกวนไฟล์อื่น) แล้วสรุปว่าไฟล์ไหนแดง
ออก exit code ไม่เป็นศูนย์ถ้ามีไฟล์ใดล้มเหลว เผื่อเอาไปต่อ CI
"""
import pathlib
import subprocess
import sys

_HERE = pathlib.Path(__file__).parent
_PKG = "story_generator.tests"


def main() -> int:
    # console ของ Windows เป็น cp1252/cp874 — ข้อความไทยของ runner เองจะพังตอน print
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    mods = sorted(p.stem for p in _HERE.glob("test_*.py"))
    if not mods:
        print("ไม่พบไฟล์เทสต์")
        return 1

    failed: list[str] = []
    for m in mods:
        # UTF-8 ชัดเจน — console ของ Windows เป็น cp874/cp1252 แล้วไฟล์ที่พิมพ์ไทย/ลูกศรจะพังเอง
        # ตั้งแต่ตอน print ไม่ใช่เพราะเทสต์ผิด
        r = subprocess.run([sys.executable, "-m", f"{_PKG}.{m}"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace",
                           env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
        out = (r.stdout or "") + (r.stderr or "")
        n_fail = sum(1 for ln in out.splitlines() if ln.startswith("FAIL"))
        crashed = "Traceback" in out
        ok = r.returncode == 0 and n_fail == 0 and not crashed
        if ok:
            n_pass = sum(1 for ln in out.splitlines() if ln.startswith("PASS"))
            print(f"  ok    {m}  ({n_pass} checks)")
        else:
            failed.append(m)
            print(f"  FAIL  {m}  ({n_fail} failed{', crashed' if crashed else ''})")
            for ln in out.splitlines():
                if ln.startswith("FAIL") or "Error" in ln:
                    print(f"          {ln[:160]}")

    print()
    if failed:
        print(f"{len(failed)}/{len(mods)} ไฟล์ล้มเหลว: {', '.join(failed)}")
        return 1
    print(f"ALL PASS — {len(mods)} ไฟล์")
    return 0


if __name__ == "__main__":
    sys.exit(main())
