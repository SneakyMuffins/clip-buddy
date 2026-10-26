"""One background thread: runs file jobs (after their undo grace period), analyzes new clips,
rescans the folders every minute. One thing at a time keeps the GPU and disk calm."""
import json
import threading
import time
import traceback

from . import actions, analyzer, config, db, scanner

INDEX_BATCH = 4   # files indexed per step, so the first clips get analyzed while a big folder is still being read

state = {
    "doing": None, "last_scan": 0.0,
    "to_index": 0,                          # files found but not indexed yet
    "analyzed_run": 0,                      # clips analyzed since the app started (for "12 of 340")
    "analyze_seconds": 0.0, "analyze_gb": 0.0,   # totals this run: the time-left estimate uses their ratio
    "priority": [],                         # clips the user is looking at: analyzed before the rest
}
_wake = threading.Event()


def wake():
    _wake.set()


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


def step():
    j = db.one("SELECT * FROM jobs WHERE state='pending' AND not_before<=? ORDER BY id LIMIT 1", (time.time(),))
    if j:
        ids = _clip_ids(j)
        db.x("UPDATE jobs SET state='running' WHERE id=?", (j["id"],))
        for i in ids:
            db.update(i, busy=1)
        state["doing"] = f"{j['kind']} #{j['clip_id'] or ''}"
        try:
            _run_job(j)
            db.x("UPDATE jobs SET state='done', finished=? WHERE id=?", (time.time(), j["id"]))
        except Exception as e:
            traceback.print_exc()
            db.x("UPDATE jobs SET state='error', error=?, finished=? WHERE id=?", (str(e)[:500], time.time(), j["id"]))
        finally:
            for i in ids:
                db.update(i, busy=0)
        return True
    did = False
    # a clip the user is looking at goes before reading more of the folder
    waiting = any(db.one("SELECT id FROM clips WHERE id=? AND analyzed=0 AND status!='trash' AND busy=0", (pid,))
                  for pid in state["priority"])
    if not waiting and config.configured() and (state["to_index"] or time.time() - state["last_scan"] > 60):
        state["doing"] = "reading folder"
        state["to_index"] = scanner.scan(max_new=INDEX_BATCH)
        if not state["to_index"]:
            state["last_scan"] = time.time()
        did = True
    c = None
    for pid in state["priority"]:
        c = db.one("SELECT id FROM clips WHERE id=? AND analyzed=0 AND status!='trash' AND busy=0", (pid,))
        if c:
            break
    c = c or db.one("SELECT id, size FROM clips WHERE analyzed=0 AND status!='trash' AND busy=0 ORDER BY start_ts LIMIT 1")
    if c and "size" not in c:
        c = db.one("SELECT id, size FROM clips WHERE id=?", (c["id"],))
    if c:
        state["doing"] = f"analyzing #{c['id']}"
        t = time.time()
        try:
            analyzer.analyze(c["id"])
        except Exception:
            traceback.print_exc()
            db.update(c["id"], analyzed=-1)
        state["analyze_seconds"] += time.time() - t
        state["analyze_gb"] += (c["size"] or 0) / 1e9
        state["analyzed_run"] += 1
        did = True
    return did


def loop():
    while True:
        try:
            busy = step()
        except Exception:
            traceback.print_exc()
            busy = False
        if not busy:
            state["doing"] = None
            _wake.wait(2)
            _wake.clear()


def eta(bytes_to_analyze):
    """Seconds left: video still to analyze (indexed or not) at the measured rate, plus indexing the rest."""
    per_file = scanner.stats["index_seconds"] / scanner.stats["indexed"] if scanner.stats["indexed"] else 0.5
    # overall rate so far, steadier than per-clip; before ~2 GB is done, lean on a typical 8 s per GB
    done_gb = state["analyze_gb"]
    sec_per_gb = (state["analyze_seconds"] + 8.0 * max(0.0, 2 - done_gb)) / (done_gb + max(0.0, 2 - done_gb))
    gb = (bytes_to_analyze + scanner.stats["pending_bytes"]) / 1e9
    return gb * sec_per_gb + state["to_index"] * per_file


def _remove_leftovers():
    """Temp files from a trim/compress/join that was cut off when the app closed (a join's can be GBs).
    Safe at startup: no job is running yet, and interrupted jobs start over from the original."""
    from pathlib import Path
    for root in [*scanner.roots(), Path(config.CFG["trash"]) if config.CFG["trash"] else None]:
        if not root or not root.exists():
            continue
        for f in root.rglob("*.cb-*"):
            if f.is_file() and f.suffix.lower() in (".mp4", ".txt"):
                try:
                    f.unlink()
                except OSError:
                    pass


def start():
    db.x("UPDATE jobs SET state='pending' WHERE state='running'")  # interrupted last time: try again
    db.x("UPDATE clips SET busy=0")
    db.x("UPDATE clips SET analyzed=0 WHERE analyzed=-1")          # failed analyses get one retry per start
    _remove_leftovers()
    threading.Thread(target=loop, daemon=True, name="clipbuddy-worker").start()
