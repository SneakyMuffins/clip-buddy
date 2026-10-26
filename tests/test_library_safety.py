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
