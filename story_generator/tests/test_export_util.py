"""export_util: copies run in a worker thread, so a slow copy does not freeze the window. Temp files only; no UI.

Run:  python -m story_generator.tests.test_export_util
"""
import asyncio
import errno
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import export_util as X  # noqa: E402

_fails: list = []


def check(name, cond, detail=""):
    if cond:
        print(f"PASS  {name}")
    else:
        _fails.append(name)
        print(f"FAIL  {name}: {detail}")


def main():
    tmp = Path(tempfile.mkdtemp(prefix="wp_export_test_"))
    try:
        srcs = []
        for i in range(4):
            p = tmp / f"s{i}.bin"
            p.write_bytes(bytes([i]) * 1000)
            srcs.append(p)
        dst = tmp / "out"
        dst.mkdir()

        progress: list = []
        res = asyncio.run(X.copy_files([(s, dst / s.name) for s in srcs], lambda d, n: progress.append((d, n))))
        check("every file is copied with its content", res["copied"] == 4 and res["failed"] == 0 and all((dst / s.name).read_bytes() == s.read_bytes() for s in srcs), res)
        check("progress is reported after every file", progress == [(1, 4), (2, 4), (3, 4), (4, 4)], progress)
        check("the time the copy took is returned", res["seconds"] >= 0)

        res = asyncio.run(X.copy_files([(tmp / "missing.bin", dst / "m.bin"), (srcs[0], dst / "again.bin")]))
        check("a missing source counts as failed and the rest still copies", res["failed"] == 1 and res["copied"] == 1 and res["errors"], res)

        # the copy must not run on the thread of the event loop, and the loop must keep running while a copy is slow
        seen: dict = {}
        real = shutil.copy2

        def slow_copy(a, b):
            seen.setdefault("threads", set()).add(threading.get_ident())
            time.sleep(0.3)
            return real(a, b)

        shutil.copy2 = slow_copy
        try:
            async def scenario():
                ticks = 0

                async def ticker():
                    nonlocal ticks
                    while True:
                        await asyncio.sleep(0.05)
                        ticks += 1

                t = asyncio.create_task(ticker())
                main_thread = threading.get_ident()
                r = await X.copy_files([(s, dst / ("slow_" + s.name)) for s in srcs[:3]])
                t.cancel()
                return r, ticks, main_thread

            r, ticks, main_thread = asyncio.run(scenario())
        finally:
            shutil.copy2 = real
        check("a slow copy runs in a worker thread, not on the event loop's thread", seen["threads"] and main_thread not in seen["threads"], seen)
        check("the event loop keeps running during a slow copy (the window would not freeze)", ticks >= 10, ticks)
        check("the slow copies still finish", r["copied"] == 3, r)

        # a full disk is raised at once (the friendly error dialog explains it), other OS errors are counted
        def full_disk(a, b):
            raise OSError(errno.ENOSPC, "No space left on device", str(b))

        shutil.copy2 = full_disk
        try:
            try:
                asyncio.run(X.copy_files([(srcs[0], dst / "x.bin")]))
                raised = False
            except OSError as exc:
                raised = exc.errno == errno.ENOSPC
        finally:
            shutil.copy2 = real
        check("disk full is raised, not hidden in a counter", raised)

        secs = asyncio.run(X.copy_one(srcs[1], dst / "one.bin"))
        check("copy_one copies and returns the seconds", (dst / "one.bin").read_bytes() == srcs[1].read_bytes() and secs >= 0)
        secs = asyncio.run(X.write_bytes(dst / "w.bin", b"abc"))
        check("write_bytes writes and returns the seconds", (dst / "w.bin").read_bytes() == b"abc" and secs >= 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print("ALL PASS" if not _fails else f"{len(_fails)} FAILED: {_fails}")
    return 0 if not _fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
