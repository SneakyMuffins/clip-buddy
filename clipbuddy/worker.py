"""One background thread: runs file jobs (after their undo grace period), analyzes new clips,
rescans the folders every minute. One thing at a time keeps the GPU and disk calm."""
import json
import threading
import time
import traceback

from . import actions, analyzer, config, db, scanner

state = {"doing": None, "last_scan": 0.0}
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
    if config.configured() and time.time() - state["last_scan"] > 60:
        state["doing"] = "scanning folders"
        scanner.scan()
        state["last_scan"] = time.time()
        return True
    c = db.one("SELECT id, path FROM clips WHERE analyzed=0 AND status!='trash' AND busy=0 ORDER BY start_ts LIMIT 1")
    if c:
        state["doing"] = f"analyzing #{c['id']}"
        try:
            analyzer.analyze(c["id"])
        except Exception:
            traceback.print_exc()
            db.update(c["id"], analyzed=-1)
        return True
    return False


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


def start():
    db.x("UPDATE jobs SET state='pending' WHERE state='running'")  # interrupted last time: try again
    db.x("UPDATE clips SET busy=0")
    threading.Thread(target=loop, daemon=True, name="clipbuddy-worker").start()
