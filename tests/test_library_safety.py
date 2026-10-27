"""Library safety: the Trash and Keepers folders stay consistent with the app's records.

Covers the cases that went wrong in practice:
- Emptying the Trash during a clip's undo window must not delete that clip (it is not in the Trash yet).
- A file another program has open is skipped, not fatal, and the freed space is still counted.
- A second copy of the app (its own records, same library folders) sees every file in Trash and Keepers.
- A file found in Trash without a record can be restored.
- Temp files left by an interrupted trim/compress/join are cleaned up at startup.

Each scenario runs in its own Python process, because the app opens its database at import.

    python -m pytest tests/test_library_safety.py      # or: python tests/test_library_safety.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _clip(folder, name, seconds):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=320x180:r=30:d={seconds}",
                    "-f", "lavfi", "-i", f"sine=f=330:r=48000:d={seconds}", "-c:v", "libx264", "-preset", "ultrafast",
                    "-g", "15", "-c:a", "aac", "-shortest", str(path)], check=True)
    old = time.time() - 3600
    os.utime(path, (old, old))
    return path


def _run(tmp, data, code):
    """Run code inside the app, with config.json pointing at the shared library and `data` as its records."""
    cfg = tmp / f"config-{data}.json"
    cfg.write_text(json.dumps({"inbox": str(tmp / "clips"), "data": str(tmp / data), "grace_seconds": 60,
                               "compress_keepers": False}))
    prelude = textwrap.dedent("""
        import sys, time
        from pathlib import Path
        from clipbuddy import actions, db, scanner, server, worker
        def check(cond, msg):
            print(("  ok   " if cond else "  FAIL ") + msg); assert cond, msg
        def run_jobs():
            while worker.step(): pass
        worker.state["last_scan"] = 0
    """)
    r = subprocess.run([sys.executable, "-c", prelude + textwrap.dedent(code)], cwd=ROOT, capture_output=True, text=True,
                       env={**os.environ, "CLIPBUDDY_CONFIG": str(cfg), "PYTHONPATH": str(ROOT)})
    print(r.stdout, end="")
    assert r.returncode == 0, r.stderr[-2000:]


def test_library_safety():
    tmp = Path(tempfile.mkdtemp(prefix="clipbuddy-safety-"))
    try:
        game = tmp / "clips" / "Test Game"
        _clip(game, "Test Game 2026.01.18 - 20.00.00.01.DVR.mp4", 8)
        _clip(game, "Test Game 2026.01.18 - 21.00.00.02.DVR.mp4", 8)
        _clip(game, "Test Game 2026.01.18 - 22.00.00.03.DVR.mp4", 8)
        (game / "Test Game 2026.01.18 - 23.00.00.04.DVR.cb-hevc.mp4").write_bytes(b"x" * 1000)   # leftover temp

        _run(tmp, "data-1", f"""
            worker._remove_leftovers()
            check(not list(Path({str(game)!r}).glob("*.cb-*")), "leftover temp file removed at startup")
            run_jobs()
            ids = {{Path(c["path"]).name[23:25]: c["id"] for c in db.q("SELECT id, path FROM clips")}}   # by hour: 20, 21, 22

            # keep one (moves to Keepers), delete one for real
            server.CFG["grace_seconds"] = 0
            server.decide(ids["20"], server.Decision(action="keep"))
            server.decide(ids["21"], server.Decision(action="trash"))
            run_jobs()

            size21 = db.clip(ids["21"])["size"]   # in Trash for real: the next Empty deletes it

            # delete the third, then empty the Trash while it is still in its undo window
            server.CFG["grace_seconds"] = 60
            server.decide(ids["22"], server.Decision(action="trash"))
            server.empty(); run_jobs()
            c3 = db.clip(ids["22"])
            check(c3 is not None and Path(c3["path"]).exists() and Path(c3["path"]).parent.name == "Test Game",
                  "empty trash during the undo window leaves that clip alone")
            server.undo()
            check(db.clip(ids["22"])["status"] == "new", "...and it can still be undone")

            # delete it for real, then empty the Trash while another program has it open
            server.CFG["grace_seconds"] = 0
            server.decide(ids["22"], server.Decision(action="trash")); run_jobs()
            locked = open(db.clip(ids["22"])["path"], "rb")
            server.empty(); run_jobs()
            err = db.one("SELECT error FROM jobs WHERE kind='empty_trash' ORDER BY id DESC LIMIT 1")["error"] or ""
            check("open in another program" in err, "locked file reported, not fatal")
            check(db.clip(ids["21"]) is None and db.stat("freed_bytes") == size21, "the other file was deleted and counted")
            check(Path(db.clip(ids["22"])["path"]).exists(), "the locked file is still in Trash")
            locked.close()

            # job order: a quick job queued after a slow one runs first
            server.bulk(server.Ids(ids=[ids["20"]], action="compress"))        # slow (encode)
            server.bulk(server.Ids(ids=[ids["22"]], action="restore"))         # quick (a move), queued later
            j, claimed = worker._next_job()
            check(j["kind"] == "restore", f"quick job goes first (picked {{j['kind']}})")
            db.x("UPDATE jobs SET state='pending' WHERE id=?", (j["id"],)); [db.update(i, busy=0) for i in claimed]

            # a clip being analyzed is not touched by a job
            worker._analyzing.add(ids["22"])
            j, claimed = worker._next_job()
            check(j["kind"] == "compress", "job on a clip being analyzed waits; the next job runs instead")
            db.x("UPDATE jobs SET state='pending' WHERE id=?", (j["id"],)); [db.update(i, busy=0) for i in claimed]
            worker._analyzing.discard(ids["22"])
            db.x("UPDATE jobs SET state='cancelled' WHERE kind='compress' AND state='pending'")
            run_jobs()

            # a clip that can't be compressed without visible loss is kept as it is, with a note, not an error
            server.CFG["vmaf_min"] = 101   # unreachable
            db.update(ids["20"], note=None)
            server.bulk(server.Ids(ids=[ids["20"]], action="compress")); run_jobs()
            c20 = db.clip(ids["20"])
            last = db.one("SELECT state, error FROM jobs WHERE kind='compress' ORDER BY id DESC LIMIT 1")
            check(last["state"] == "done" and (c20["note"] or "").startswith("Not compressed") and c20["vcodec"] == "h264",
                  "quality bar missed: kept at full quality with a note, job not failed")
            server.CFG["vmaf_min"] = 95

            # leave one clip in Trash for the second-copy check below
            server.CFG["grace_seconds"] = 0
            server.decide(ids["22"], server.Decision(action="trash")); run_jobs()
            check(Path(db.clip(ids["22"])["path"]).parent.name == "Trash", "clip back in Trash for the next part")
        """)

        trash = tmp / "Clip Buddy Library" / "Trash"
        in_trash = [f for f in Path(trash).resolve().iterdir() if f.suffix == ".mp4"]
        folder_bytes = sum(f.stat().st_size for f in in_trash)

        _run(tmp, "data-2", f"""
            # a second copy of the app: empty records, same library folders
            run_jobs()
            rows = db.q("SELECT * FROM clips WHERE status='trash'")
            check(len(rows) == {len(in_trash)} and sum(r["size"] for r in rows) == {folder_bytes},
                  f"second copy sees the Trash folder: {{len(rows)}} file(s), same size as the folder")
            kept = db.q("SELECT * FROM clips WHERE path LIKE ?", ("%Keepers%",))
            check(len(kept) == 1 and kept[0]["status"] == "keep", "a clip in Keepers counts as kept, not unreviewed")
            found = rows[0]
            server.bulk(server.Ids(ids=[found["id"]], action="restore")); run_jobs()
            back = db.clip(found["id"])
            check(back["status"] == "new" and Path(back["path"]).exists() and "Keepers" not in back["path"]
                  and back["analyzed"] == 1, "a found Trash file restores into the clips folder, and is analyzed")
        """)
        print("all checks passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_kept_clip_is_never_lost():
    """A kept clip must survive every failure on the way: some clips lost their only copy when a step
    after 'original to Trash' failed, the startup cleanup removed the compressed copy, and the Trash was
    emptied. Names that don't follow NVIDIA's pattern (Windows trims) triggered the failing step."""
    tmp = Path(tempfile.mkdtemp(prefix="clipbuddy-keep-"))
    try:
        game = tmp / "clips" / "Test Game"
        _clip(game, "Test Game 2026.01.18 - 20.00.00.01.DVR-00.00.01.000-00.00.07.000.mp4", 6)
        _clip(game, "Test Game 2026.01.18 - 21.00.00.02.DVR.mp4", 6)
        _clip(game, "Test Game 2026.01.18 - 22.00.00.03.DVR.mp4", 6)
        _clip(game, "Test Game 2026.01.18 - 23.00.00.04.DVR.mp4", 6)

        _run(tmp, "data", """
            import os, shutil
            run_jobs()
            server.CFG.update(grace_seconds=0, encoder="x265", vmaf_min=0)
            ids = {Path(c["path"]).name[23:25]: c["id"] for c in db.q("SELECT id, path FROM clips")}

            # 1. a trimmed-by-Windows name: keep + compress completes, the original waits in Trash
            actions.keep(ids["20"], do_compress=True)
            k = db.clip(ids["20"])
            orig = db.one("SELECT * FROM clips WHERE status='trash' AND prev_path=?", (k["path"],))
            check(Path(k["path"]).exists() and k["compressed"] == 1 and orig and Path(orig["path"]).exists(),
                  "odd file name: kept, compressed, original recorded in Trash")

            # 2. the swap fails half way: the original goes back where it was
            actions.keep(ids["21"], do_compress=False)
            k = db.clip(ids["21"]); before = Path(k["path"]).stat().st_size
            real = os.replace
            def broken(a, b): raise OSError("disk full")
            actions.os.replace = broken
            try:
                actions.compress(ids["21"])
            except OSError:
                pass
            actions.os.replace = real
            check(Path(k["path"]).exists() and Path(k["path"]).stat().st_size == before
                  and not db.one("SELECT id FROM clips WHERE prev_path=?", (k["path"],)),
                  "failed swap: the original is back in Keepers, nothing left in Trash")

            # 3. the app closed between 'original to Trash' and 'new file in place': startup finishes it
            actions.keep(ids["22"], do_compress=False)
            p = Path(db.clip(ids["22"])["path"]); new = p.with_name(p.stem + ".cb-hevc.mp4")
            shutil.copy2(p, new)
            actions.to_trash(p, note="original, replaced by the compressed version", status_from="keep")
            worker._remove_leftovers()
            check(p.exists() and not new.exists(), "startup puts a finished copy in place instead of deleting it")

            # 4. Empty trash keeps an original whose kept copy is missing
            actions.keep(ids["23"], do_compress=False)
            p = Path(db.clip(ids["23"])["path"])
            tid = actions.to_trash(p, note="original, replaced by the compressed version", status_from="keep")
            actions.empty_trash()
            check(db.clip(tid) and Path(db.clip(tid)["path"]).exists(),
                  "empty trash never deletes the only copy of a kept clip")
            check(not Path(orig["path"]).exists(), "...but does delete an original whose copy is safe")
        """)

        _run(tmp, "data", """
            # 5. a job moves a clip while the folder walk is under way: its record must not be dropped
            run_jobs()
            c = db.q("SELECT * FROM clips WHERE path LIKE ? ORDER BY id", ("%Keepers%",))[0]
            src = Path(c["path"]); moved = src.with_name("moved.mp4")
            real = scanner.walk
            def walk_while_moving():
                for i, p in enumerate(real()):
                    if i == 0:
                        src.rename(moved); db.update(c["id"], path=str(moved))
                    yield p
            scanner.walk = walk_while_moving
            scanner._walk_pending()
            check(db.clip(c["id"]) is not None, "a clip moved during the walk keeps its record")
        """)

        _run(tmp, "data", """
            # 6. a library left broken by the old bug is repaired on the first start of the update
            import shutil
            trash = Path(server.CFG["trash"])
            kept = [Path(c["path"]) for c in db.q("SELECT path FROM clips WHERE path LIKE ? ORDER BY id", ("%Keepers%",))]
            a, b = kept[0], kept[1]
            shutil.copy2(a, a.with_name(a.stem + ".cb-hevc.mp4"))     # compressed copy left behind,
            a.rename(trash / a.name)                                  # original in Trash with no record
            b.rename(trash / b.name)                                  # compressed copy already cleaned up
            db.x("DELETE FROM clips WHERE path IN (?, ?)", (str(a), str(b)))
            db.add_job(None, "keep")
            db.x("UPDATE jobs SET state='error', error=? WHERE id=(SELECT max(id) FROM jobs)",
                 (str(FileNotFoundError(2, "The system cannot find the file specified", str(b))),))
            run_jobs()   # the scan takes the Trash files in as 'found in the Trash folder'
            worker._remove_leftovers(); worker._repair_failed_keeps()
            check(a.exists() and db.one("SELECT prev_status FROM clips WHERE path=?", (str(trash / a.name),))["prev_status"] == "keep",
                  "left-behind copy put in place, its original marked as a kept clip's original")
            check(b.exists() and not (trash / b.name).exists() and db.one("SELECT status FROM clips WHERE path=?", (str(b),))["status"] == "keep",
                  "failed keep: the original is moved back to Keepers as kept")
        """)
        print("all checks passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_api_only_answers_this_pc():
    """Another website must not be able to change anything (for example empty the Trash)."""
    import urllib.error
    import urllib.request
    tmp = Path(tempfile.mkdtemp(prefix="clipbuddy-api-"))
    port = 8799
    cfg = tmp / "config.json"
    cfg.write_text(json.dumps({"inbox": str(tmp / "clips"), "data": str(tmp / "data"), "port": port}))
    (tmp / "clips").mkdir()
    server = subprocess.Popen([sys.executable, "-m", "clipbuddy"], cwd=ROOT,
                              env={**os.environ, "CLIPBUDDY_CONFIG": str(cfg), "CLIPBUDDY_NO_BROWSER": "1"},
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://localhost:{port}"

    def call(path, method="GET", headers=None, data=b""):
        req = urllib.request.Request(url + path, data=data if method == "POST" else None, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    try:
        for _ in range(60):
            try:
                if call("/api/setup") == 200:
                    break
            except Exception:
                time.sleep(0.5)
        checks = {
            "the page itself can read": call("/api/status") == 200,
            "a change without the header is refused (cross-site form post)": call("/api/trash/empty", "POST") == 403,
            "a change with the header works (the app's own page)":
                call("/api/trash/empty", "POST", {"X-Clip-Buddy": "1", "Content-Type": "application/json"}, b"{}") == 200,
            "a request for another host name is refused (DNS rebinding)":
                call("/api/status", headers={"Host": f"evil.example:{port}"}) == 400,
        }
        for msg, ok in checks.items():
            print(("  ok   " if ok else "  FAIL ") + msg)
        assert all(checks.values())
    finally:
        server.terminate()
        server.wait(10)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_library_safety()
    test_api_only_answers_this_pc()
