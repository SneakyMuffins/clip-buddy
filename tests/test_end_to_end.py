"""End-to-end test on synthetic clips (never touches a real library).

Builds a temporary library from ffmpeg-generated clips, then drives the same code the app uses:
moment detection, keep (move + trim + compress + quality check), delete, undo, restore, join, empty trash.

    python -m pytest tests                   # or: python tests/test_end_to_end.py
    python tests/test_end_to_end.py x265     # force one encoder (nvenc / amf / qsv / x265)

CLIPBUDDY_TEST_ENCODER=x265 does the same under pytest.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="clipbuddy-test-"))
(TMP / "config.json").write_text(json.dumps({
    "inbox": str(TMP / "inbox"), "keepers": str(TMP / "keepers"), "trash": str(TMP / "trash"),
    "data": str(TMP / "data"), "grace_seconds": 0,
    "encoder": os.environ.get("CLIPBUDDY_TEST_ENCODER")
               or (sys.argv[1] if __name__ == "__main__" and len(sys.argv) > 1 else "auto")}))
os.environ["CLIPBUDDY_CONFIG"] = str(TMP / "config.json")
sys.path.insert(0, str(ROOT))

GAME = "Test Game"
T0 = datetime(2026, 1, 18, 20, 0, 0)


def make_clip(save_time, seconds, burst_at=None):
    """A replay-style clip: moving test pattern + quiet tone; optional loud noise + scene flash at burst_at."""
    d = TMP / "inbox" / GAME
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{GAME} {save_time:%Y.%m.%d - %H.%M.%S}.01.DVR.mp4"
    audio = "sine=f=220:r=48000,volume=0.05"
    video = f"testsrc2=s=640x360:r=60:d={seconds}"
    if burst_at is not None:
        audio = (f"sine=f=220:r=48000:d={seconds},volume=0.05[a];anoisesrc=r=48000:d={seconds}:a=0.8,"
                 f"volume='if(between(t,{burst_at},{burst_at + 2}),1,0)':eval=frame[n];[a][n]amix=normalize=0")
        video += f",drawbox=c=white:t=fill:enable='between(t,{burst_at},{burst_at + 2})'"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", video, "-f", "lavfi", "-t", str(seconds),
                    "-i", audio, "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-g", "30",
                    "-c:a", "aac", "-shortest", str(path)], check=True)
    old = time.time() - 3600
    os.utime(path, (old, old))
    return path


def check(cond, msg):
    print(("  ok   " if cond else "  FAIL ") + msg)
    assert cond, msg


def run_jobs():
    from clipbuddy import worker
    while worker.step():
        pass


def test_end_to_end():
    print(f"test library: {TMP}")
    big = make_clip(T0, 60, burst_at=45)                                    # moment at 45 s
    make_clip(T0 + timedelta(minutes=10), 30)                                # back-to-back pair:
    make_clip(T0 + timedelta(minutes=10, seconds=12), 12)                    #   the second starts where the first ended
    tiny = make_clip(T0 + timedelta(minutes=20), 1.5)

    from clipbuddy import db, encoders, scanner, server, worker
    check(scanner.scan(max_new=2) == 2, "indexing in batches: 2 indexed, 2 still waiting")
    check(scanner.scan() == 0, "rest of the folder indexed")

    # the clip on screen jumps the analysis line: the newest clip goes before the three older ones
    tiny_id = db.one("SELECT id FROM clips WHERE path=?", (str(tiny),))["id"]
    server.prioritize(server.Ids(ids=[tiny_id]))
    worker.state["last_scan"] = time.time()
    worker.step()
    done = {c["id"] for c in db.q("SELECT id FROM clips WHERE analyzed=1")}
    check(done == {tiny_id}, "prioritized clip analyzed first")
    st = server.status()
    check(st["to_analyze"] == 3 and st["to_index"] == 0 and st["eta_seconds"] > 0,
          f"progress reported: {st['to_analyze']} to analyze, about {st['eta_seconds']} s left")

    worker.state["last_scan"] = 0
    run_jobs()                                                               # scan + analyze everything
    clips = {Path(c["path"]).name: c for c in db.q("SELECT * FROM clips")}
    check(len(clips) == 4, "4 clips indexed")
    cb = clips[big.name]
    check(abs(cb["peak"] - 46) < 4, f"moment found near the burst (peak {cb['peak']:.1f}s, burst 45-47s)")
    check(cb["sug_start"] < 45 < 47 < cb["sug_end"], f"suggested range {cb['sug_start']}-{cb['sug_end']} covers the burst")
    check(clips[tiny.name]["quiet"] == 1, "tiny clip flagged")

    chains = server.chains()
    check(len(chains) == 1 and len(chains[0]["ids"]) == 2, "back-to-back pair detected")

    # keep with the suggested range: move + trim + compress (+ VMAF check)
    server.decide(cb["id"], server.Decision(action="keep", start=cb["sug_start"], end=cb["sug_end"], rating=5, title="big moment"))
    run_jobs()
    k = db.clip(cb["id"])
    err = db.one("SELECT error FROM jobs WHERE state='error'")
    check(err is None, f"keep job ran without error {err or ''}")
    check(Path(k["path"]).parent == TMP / "keepers" / GAME, "kept clip moved to Keepers/<Game>")
    check(k["trimmed"] == 1 and abs(k["duration"] - (cb["sug_end"] - cb["sug_start"])) < 1.5, f"trimmed to {k['duration']:.1f}s")
    check(k["compressed"] == 1 and k["vcodec"] == "hevc", f"compressed to HEVC with {encoders.label()}")
    check("big moment" in Path(k["path"]).name and k["rating"] == 5, "title renamed the file, rating stored")
    check(db.one("SELECT COUNT(*) n FROM clips WHERE status='trash'")["n"] == 2, "both replaced originals are in Trash")

    # delete + undo inside the grace period
    actions_cfg = server.CFG
    actions_cfg["grace_seconds"] = 60
    ct = clips[tiny.name]
    server.decide(ct["id"], server.Decision(action="trash"))
    server.undo()
    run_jobs()
    check(db.clip(ct["id"])["status"] == "new" and Path(db.clip(ct["id"])["path"]).exists(), "undo before grace ran: file untouched")

    # delete for real, then restore
    actions_cfg["grace_seconds"] = 0
    server.decide(ct["id"], server.Decision(action="trash"))
    run_jobs()
    check(Path(db.clip(ct["id"])["path"]).parent == TMP / "trash", "deleted clip is in Trash folder")
    server.bulk(server.Ids(ids=[ct["id"]], action="restore"))
    run_jobs()
    check(db.clip(ct["id"])["status"] == "new" and Path(db.clip(ct["id"])["path"]) == tiny, "restored to its original place")

    # join the back-to-back pair
    server.merge(server.Ids(ids=chains[0]["ids"]))
    run_jobs()
    joined = db.one("SELECT * FROM clips WHERE path LIKE '%(joined 2)%'")
    check(joined is not None and abs(joined["duration"] - 42) < 1.5, f"joined clip is {joined and joined['duration']:.1f}s (30 + 12)")

    # empty trash
    n_trash = db.one("SELECT COUNT(*) n, SUM(size) s FROM clips WHERE status='trash'")
    server.empty()
    run_jobs()
    left = list((TMP / "trash").iterdir())
    check(not left and db.stat("freed_bytes") == n_trash["s"], f"trash emptied, {n_trash['n']} files freed")
    print("all checks passed")
    db._conn.close()
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    test_end_to_end()
