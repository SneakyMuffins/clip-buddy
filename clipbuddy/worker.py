"""Background work, in two lanes that run at the same time:

- jobs: file changes from your decisions. Quick ones (deletes, moves, renames) have their own lane, so they
  never wait behind slow ones (trim + compress, join), which run one at a time.
- analysis: reading the folder and analyzing clips, on several threads when the PC has the cores
  (measured on an 8-core/16-thread CPU: 4 at once was 3.9x faster than 1; 6 or 8 were no faster).

A clip is claimed by one lane at a time, so it is never analyzed while a job moves or trims it.
"""
import json
import os
import re
import shutil
import threading
import time
import traceback
from pathlib import Path

from . import actions, analyzer, config, db, scanner

INDEX_BATCH = 4    # files indexed per step, so the first clips get analyzed while a big folder is still being read
QUICK_JOBS = ("trash", "temp", "restore", "rename", "empty_trash")   # moves and renames: seconds at most

state = {
    "doing": None, "last_scan": 0.0,
    "to_index": 0,                          # files found but not indexed yet
    "analyzed_run": 0,                      # clips analyzed since the app started (for "12 of 340")
    "analyze_seconds": 0.0, "analyze_gb": 0.0,   # totals this run: the time-left estimate uses their ratio
    "job_seconds": {},                      # kind -> [total seconds, count] this run, for the jobs estimate
    "priority": [],                         # clips the user is looking at: analyzed before the rest
    "workers": 1,
}
_wake = threading.Condition()
_claim = threading.Lock()
_analyzing = set()        # clip ids an analysis thread is working on
_scan_lock = threading.Lock()


def wake():
    with _wake:
        _wake.notify_all()


def analysis_workers():
    n = config.CFG.get("analysis_workers", "auto")
    if isinstance(n, int) and n > 0:
        return n
    return max(1, min(4, (os.cpu_count() or 4) // 4))


# ---------- jobs ----------

def _run_job(j):
    args = json.loads(j["args"] or "{}")
    cid = j["clip_id"]
    if j["kind"] == "trash":
        actions.trash(cid)
    elif j["kind"] == "temp":
        pass  # status already set; the job only exists so the decision can be undone like the others
    elif j["kind"] == "keep":
        actions.keep(cid, args.get("start"), args.get("end"))
    elif j["kind"] == "compress":
        actions.compress(cid)
    elif j["kind"] == "restore":
        actions.restore(cid)
    elif j["kind"] == "rename":
        actions.rename(cid, args.get("title"))
    elif j["kind"] == "merge":
        actions.merge(args["ids"])
    elif j["kind"] == "empty_trash":
        actions.empty_trash()
    else:
        raise actions.ActionError(f"unknown job {j['kind']}")


def _clip_ids(j):
    if j["kind"] == "merge":
        return json.loads(j["args"])["ids"]
    return [j["clip_id"]] if j["clip_id"] else []


def _next_job(lane=None):
    """Oldest due job, quick kinds first, whose clips are not being analyzed right now. Claims its clips.
    lane "quick" takes only moves/renames, lane "slow" only encodes; None (the tests) takes either."""
    quick = ",".join(f"'{k}'" for k in QUICK_JOBS)
    only = {"quick": f"AND kind IN ({quick})", "slow": f"AND kind NOT IN ({quick})"}.get(lane, "")
    jobs = db.q(f"SELECT * FROM jobs WHERE state='pending' AND not_before<=? {only} "
                f"ORDER BY CASE WHEN kind IN ({quick}) THEN 0 ELSE 1 END, id LIMIT 20", (time.time(),))
    with _claim:
        for j in jobs:
            ids = _clip_ids(j)
            if any(i in _analyzing for i in ids):
                continue
            db.x("UPDATE jobs SET state='running' WHERE id=?", (j["id"],))
            for i in ids:
                db.update(i, busy=1)
            return j, ids
    return None, []


def job_step(lane=None):
    j, ids = _next_job(lane)
    if not j:
        return False
    if lane != "quick":
        state["doing"] = f"{j['kind']} #{j['clip_id'] or ''}"
    t = time.time()
    try:
        _run_job(j)
        db.x("UPDATE jobs SET state='done', finished=? WHERE id=?", (time.time(), j["id"]))
    except Exception as e:
        traceback.print_exc()
        db.x("UPDATE jobs SET state='error', error=?, finished=? WHERE id=?", (str(e)[:500], time.time(), j["id"]))
    finally:
        for i in ids:
            db.update(i, busy=0)
        if lane != "quick":
            state["doing"] = None
    total, n = state["job_seconds"].get(j["kind"], [0.0, 0])
    state["job_seconds"][j["kind"]] = [total + time.time() - t, n + 1]
    return True


# ---------- reading the folder + analysis ----------

def _scan_step():
    """One batch of folder reading, done by whichever analysis thread gets here first."""
    if not config.configured() or not (state["to_index"] or time.time() - state["last_scan"] > 60):
        return False
    if not _scan_lock.acquire(blocking=False):
        return False
    try:
        state["to_index"] = scanner.scan(max_new=INDEX_BATCH)
        if not state["to_index"]:
            state["last_scan"] = time.time()
        return True
    finally:
        _scan_lock.release()


def _claim_clip():
    """Next clip to analyze: ones on screen first, then unreviewed clips oldest first, then the rest
    (kept clips re-analyzed after a trim only need fresh thumbnails, so they go last)."""
    with _claim:
        candidates = [db.one("SELECT id, size FROM clips WHERE id=? AND analyzed=0 AND status!='trash' AND busy=0", (pid,))
                      for pid in state["priority"]]
        candidates += db.q("SELECT id, size FROM clips WHERE analyzed=0 AND status!='trash' AND busy=0 "
                           "ORDER BY CASE status WHEN 'new' THEN 0 ELSE 1 END, start_ts LIMIT 20")
        for c in candidates:
            if c and c["id"] not in _analyzing:
                _analyzing.add(c["id"])
                return c
    return None


def analysis_step():
    on_screen_waiting = any(db.one("SELECT id FROM clips WHERE id=? AND analyzed=0 AND status!='trash' AND busy=0", (p,))
                            for p in state["priority"])
    did = False if on_screen_waiting else _scan_step()   # a clip the user is looking at goes before reading more
    c = _claim_clip()
    if not c:
        return did
    t = time.time()
    try:
        analyzer.analyze(c["id"])
    except Exception:
        traceback.print_exc()
        db.update(c["id"], analyzed=-1)
    finally:
        with _claim:
            _analyzing.discard(c["id"])
    state["analyze_seconds"] += time.time() - t
    state["analyze_gb"] += (c["size"] or 0) / 1e9
    state["analyzed_run"] += 1
    return True


def step():
    """One unit of work, jobs first (used by the tests; the app runs the two lanes in parallel)."""
    return job_step() or analysis_step()


def _lane(fn, name):
    while True:
        try:
            busy = fn()
        except Exception:
            traceback.print_exc()
            busy = False
        if not busy:
            with _wake:
                _wake.wait(2)


# ---------- progress ----------

def eta(bytes_to_analyze):
    """Seconds left for analysis: video still to analyze (indexed or not) at the measured rate, split over
    the analysis threads, plus indexing the rest."""
    per_file = scanner.stats["index_seconds"] / scanner.stats["indexed"] if scanner.stats["indexed"] else 0.5
    # overall rate so far, steadier than per-clip; before ~2 GB is done, lean on a typical 8 s per GB per thread
    done_gb = state["analyze_gb"]
    sec_per_gb = (state["analyze_seconds"] + 8.0 * max(0.0, 2 - done_gb)) / (done_gb + max(0.0, 2 - done_gb))
    gb = (bytes_to_analyze + scanner.stats["pending_bytes"]) / 1e9
    return gb * sec_per_gb / state["workers"] + state["to_index"] * per_file


def jobs_eta(pending):
    """Seconds left for the job lane: measured average per kind (or a typical guess) times what's queued."""
    typical = {"keep": 45.0, "compress": 45.0, "merge": 20.0}
    total = 0.0
    for j in pending:
        t, n = state["job_seconds"].get(j["kind"], [0.0, 0])
        total += t / n if n else typical.get(j["kind"], 1.0)
    return total


# ---------- start ----------

def _in_trash(name):
    trash = config.CFG["trash"]
    return Path(trash) / name if trash and (Path(trash) / name).exists() else None


def _remove_leftovers():
    """Temp files from a trim/compress/join that was cut off when the app closed (a join's can be GBs).
    Safe at startup: no job is running yet, and interrupted jobs start over from the original.
    A finished trim/compress whose original already went to the Trash is the clip itself: it is put in
    place, never deleted."""
    for root in [*scanner.roots(), Path(config.CFG["trash"]) if config.CFG["trash"] else None]:
        if not root or not root.exists():
            continue
        for f in root.rglob("*.cb-*"):
            if not (f.is_file() and f.suffix.lower() in (".mp4", ".txt")):
                continue
            m = re.match(r"^(.+)\.cb-(?:hevc|trim)\.mp4$", f.name)
            original = f.with_name(m[1] + ".mp4") if m else None
            try:
                if original and not original.exists() and _in_trash(original.name):
                    os.replace(f, original)
                    _mark_original(_in_trash(original.name), original)
                else:
                    f.unlink()
            except OSError:
                pass


def _mark_original(trashed, kept):
    t = db.one("SELECT id FROM clips WHERE path=?", (str(trashed),))
    if t:
        db.update(t["id"], prev_path=str(kept), prev_status="keep", note="original, replaced by the kept version")


def _repair_failed_keeps():
    """Up to 0.1.4, keeping a clip whose name doesn't follow NVIDIA's pattern could fail after its original
    had gone to the Trash, leaving the kept clip missing from Keepers. Put those originals back as kept."""
    keepers = config.CFG["keepers"]
    if not keepers:
        return
    for j in db.q("SELECT id, error FROM jobs WHERE state='error' AND kind IN ('keep', 'compress')"):
        for raw in re.findall(r"'([^']+\.mp4)'", j["error"] or ""):
            kept = Path(raw.replace("\\\\", "\\"))
            trashed = _in_trash(kept.name)
            if not scanner._inside(kept, keepers) or kept.exists() or not trashed:
                continue
            kept.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(trashed), str(kept))
            db.x("DELETE FROM clips WHERE path=?", (str(trashed),))
            cid = scanner.index_file(kept, status="keep")
            db.add_job(cid, "compress")
            db.x("UPDATE jobs SET state='done', error=? WHERE id=?", ("repaired: the original is back in Keepers", j["id"]))


def start():
    db.x("UPDATE jobs SET state='pending' WHERE state='running'")  # interrupted last time: try again
    db.x("UPDATE clips SET busy=0")
    db.x("UPDATE clips SET analyzed=0 WHERE analyzed=-1")          # failed analyses get one retry per start
    _remove_leftovers()
    _repair_failed_keeps()
    state["workers"] = analysis_workers()
    # quick jobs (deletes, moves, renames) get their own lane so they never wait behind an encode
    threading.Thread(target=_lane, args=(lambda: job_step("quick"), "quick jobs"), daemon=True, name="clipbuddy-quick").start()
    threading.Thread(target=_lane, args=(lambda: job_step("slow"), "slow jobs"), daemon=True, name="clipbuddy-slow").start()
    for i in range(state["workers"]):
        threading.Thread(target=_lane, args=(analysis_step, "analysis"), daemon=True, name=f"clipbuddy-analysis-{i}").start()
