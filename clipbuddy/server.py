"""Web API + UI. Run: python -m clipbuddy  (then open http://localhost:8765)."""
import asyncio
import json
import os
import shutil
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import actions, config, db, encoders, worker
from .config import CFG, DATA

STATIC = Path(__file__).parent / "static"

FIELDS = ("id, path, game, kind, start_ts, duration, size, width, height, fps, vcodec, status, expires, rating, "
          "title, tags, keep_start, keep_end, sug_start, sug_end, sug_whole, peak, strength, quiet, analyzed, "
          "trimmed, compressed, orig_size, busy, note, prev_path, trashed_at, reviewed_at")


def _out(c):
    c["name"] = os.path.basename(c["path"])
    return c


def _need(cid):
    c = db.clip(cid)
    if not c:
        raise HTTPException(404, "no such clip")
    return c


@asynccontextmanager
async def _lifespan(app):
    # the browser drops video connections whenever you seek; Windows asyncio logs each one as an error
    loop = asyncio.get_running_loop()

    def quiet(loop, ctx):
        if isinstance(ctx.get("exception"), ConnectionResetError):
            return
        loop.default_exception_handler(ctx)
    loop.set_exception_handler(quiet)
    worker.start()
    yield


app = FastAPI(title="Clip Buddy", lifespan=_lifespan)


# ---------- reading ----------

_analysis_cache = {}


def _analysis(cid):
    f = DATA / "analysis" / f"{cid}.json"
    try:
        mt = f.stat().st_mtime
    except FileNotFoundError:
        return None
    hit = _analysis_cache.get(cid)
    if not hit or hit[0] != mt:
        hit = _analysis_cache[cid] = (mt, json.loads(f.read_text()))
    return hit[1]


@app.get("/api/clips")
def clips(status: str = "all"):
    if status == "trash":
        rows = db.q(f"SELECT {FIELDS} FROM clips WHERE status='trash' ORDER BY trashed_at DESC")
        return [_out(c) for c in rows]
    rows = db.q(f"SELECT {FIELDS} FROM clips WHERE status!='trash' ORDER BY start_ts")
    for c in rows:
        _out(c)
        c["analysis"] = _analysis(c["id"]) if c["analyzed"] == 1 else None
    return rows


@app.get("/api/clip/{cid}")
def clip(cid: int):
    c = _out(_need(cid))
    c["analysis"] = _analysis(cid)
    return c


@app.get("/api/chains")
def chains():
    return [{"ids": [c["id"] for c in ch], "duration": sum(c["duration"] for c in ch)} for ch in actions.chains()]


@app.get("/api/status")
def status():
    pend = db.q("SELECT j.*, c.path FROM jobs j LEFT JOIN clips c ON c.id=j.clip_id "
                "WHERE state IN ('pending','running') ORDER BY j.id")
    errs = db.q("SELECT j.*, c.path FROM jobs j LEFT JOIN clips c ON c.id=j.clip_id "
                "WHERE state='error' AND finished>? ORDER BY j.id DESC LIMIT 5", (time.time() - 3600,))
    left = db.one("SELECT COUNT(*) n FROM clips WHERE analyzed=0 AND status!='trash'")["n"]
    tr = db.one("SELECT COUNT(*) n, SUM(size) s FROM clips WHERE status='trash'")
    w = worker.state
    eta = left * w["sec_analyze"] + w["to_index"] * (w["sec_index"] + w["sec_analyze"])
    return {"doing": w["doing"], "jobs": pend, "errors": errs, "to_analyze": left,
            "to_index": w["to_index"], "analyzed_run": w["analyzed_run"], "eta_seconds": round(eta),
            "trash": {"n": tr["n"], "size": tr["s"] or 0}}



@app.get("/api/stats")
def stats():
    rows = db.q("SELECT game, status, vcodec, COUNT(*) n, SUM(size) size, SUM(COALESCE(orig_size, size)) orig "
                "FROM clips GROUP BY game, status, vcodec")
    games = {}
    tot = {"new": 0, "keep": 0, "temp": 0, "trash": 0}
    compressible = 0
    for r in rows:
        g = games.setdefault(r["game"], {"game": r["game"], "n": 0, "size": 0, "by": {}})
        if r["status"] != "trash":
            g["n"] += r["n"]
            g["size"] += r["size"] or 0
        g["by"][r["status"]] = g["by"].get(r["status"], 0) + (r["size"] or 0)
        tot[r["status"]] = tot.get(r["status"], 0) + (r["size"] or 0)
        if r["status"] == "keep" and r["vcodec"] == "h264":
            compressible += r["size"] or 0
    saved_now = db.one("SELECT SUM(orig_size - size) s FROM clips WHERE orig_size IS NOT NULL AND status!='trash'")["s"] or 0
    junk = db.one("SELECT COUNT(*) n, SUM(size) s FROM clips WHERE status='new' AND (quiet=1 OR duration<5)")
    expired = db.one("SELECT COUNT(*) n, SUM(size) s FROM clips WHERE status='temp' AND expires<?", (time.time(),))
    sug = db.q("SELECT * FROM suggestions")
    hits = 0
    for s in sug:
        if s["final_start"] is None:
            hits += s["sug_whole"] == 1
        else:
            a, b = max(s["sug_start"], s["final_start"]), min(s["sug_end"], s["final_end"])
            hits += (b - a) >= 0.5 * (s["final_end"] - s["final_start"])
    du = shutil.disk_usage(CFG["inbox"]) if CFG["inbox"] and os.path.exists(CFG["inbox"]) else None
    return {
        "games": sorted(games.values(), key=lambda g: -g["size"]),
        "totals": tot,
        "compressible": compressible, "compress_saves": int(compressible * 0.77),
        "trimmed_or_compressed_saved": saved_now, "freed": db.stat("freed_bytes"),
        "junk": junk, "expired": expired,
        "suggestions": {"n": len(sug), "hits": hits},
        "disk": {"free": du.free, "total": du.total} if du else None,
        "settings": {**{k: CFG[k] for k in ("inbox", "keepers", "trash", "compress_keepers", "cq", "temp_days")},
                     "encoder": encoders.label()},
    }


# ---------- deciding ----------

class Decision(BaseModel):
    action: str                  # keep | temp | trash
    start: float | None = None   # keep range; both None = whole clip
    end: float | None = None
    rating: int | None = None
    title: str | None = None
    tags: str | None = None


@app.post("/api/clip/{cid}/decide")
def decide(cid: int, d: Decision):
    c = _need(cid)
    if c["busy"]:
        raise HTTPException(409, "clip is being processed")
    grace = CFG["grace_seconds"]
    # a newer decision replaces a pending one; "previous status" stays what it was before that one
    prev = c["prev_status"] if _pending_decision(cid) else c["status"]
    db.x("UPDATE jobs SET state='cancelled' WHERE clip_id=? AND state='pending' AND kind IN ('keep','trash','temp')", (cid,))
    fields = dict(prev_status=prev, reviewed_at=time.time())
    if d.rating is not None:
        fields["rating"] = d.rating or None
    if d.tags is not None:
        fields["tags"] = d.tags.strip() or None
    if d.action == "keep":
        whole = d.start is None or d.end is None
        fields.update(status="keep", keep_start=None if whole else d.start, keep_end=None if whole else d.end)
        db.x("INSERT OR REPLACE INTO suggestions VALUES (?,?,?,?,?,?,?)",
             (cid, c["sug_start"], c["sug_end"], c["sug_whole"], None if whole else d.start,
              None if whole else d.end, c["duration"]))
        db.update(cid, **fields)
        db.add_job(cid, "keep", {"start": None if whole else d.start, "end": None if whole else d.end}, delay=grace)
    elif d.action == "temp":
        db.update(cid, **fields, status="temp", expires=time.time() + CFG["temp_days"] * 86400)
        db.add_job(cid, "temp", delay=grace)
    elif d.action == "trash":
        db.update(cid, **fields, status="trash")
        db.add_job(cid, "trash", delay=grace)
    else:
        raise HTTPException(400, "unknown action")
    if d.title is not None and (d.title.strip() or None) != c["title"]:
        db.add_job(cid, "rename", {"title": d.title.strip()}, delay=grace + 0.5)
    worker.wake()
    return {"ok": True}


def _pending_decision(cid):
    return db.one("SELECT id FROM jobs WHERE clip_id=? AND state='pending' AND kind IN ('keep','trash','temp')", (cid,))


class UndoReq(BaseModel):
    ids: list[int] | None = None


@app.post("/api/undo")
def undo(u: UndoReq | None = None):
    """Cancels decisions whose grace period has not run out: the given clips, else the newest one."""
    ids = (u.ids if u else None) or []
    if not ids:
        j = db.one("SELECT * FROM jobs WHERE state='pending' AND kind IN ('keep','trash','temp') ORDER BY id DESC LIMIT 1")
        ids = [j["clip_id"]] if j else []
    done = []
    for cid in ids:
        if not _pending_decision(cid):
            continue
        db.x("UPDATE jobs SET state='cancelled' WHERE clip_id=? AND state='pending'", (cid,))
        c = db.clip(cid)
        db.update(cid, status=c["prev_status"] or "new", reviewed_at=None, expires=None,
                  keep_start=None, keep_end=None)
        db.x("DELETE FROM suggestions WHERE clip_id=?", (cid,))
        done.append(cid)
    if not done:
        raise HTTPException(404, "too late to undo: it already ran (restore it from Trash instead)")
    return {"id": done[0], "ids": done}


class Meta(BaseModel):
    rating: int | None = None
    title: str | None = None
    tags: str | None = None


@app.post("/api/clip/{cid}/meta")
def meta(cid: int, m: Meta):
    c = _need(cid)
    if m.rating is not None:
        db.update(cid, rating=m.rating or None)
    if m.tags is not None:
        db.update(cid, tags=m.tags.strip() or None)
    if m.title is not None and (m.title.strip() or None) != c["title"]:
        db.add_job(cid, "rename", {"title": m.title.strip()})
        worker.wake()
    return {"ok": True}


class Ids(BaseModel):
    ids: list[int]
    action: str | None = None


@app.post("/api/bulk")
def bulk(b: Ids):
    """trash | compress | restore for many clips."""
    if b.action not in ("trash", "compress", "restore"):
        raise HTTPException(400, "unknown action")
    for cid in b.ids:
        c = db.clip(cid)
        if not c or c["busy"]:
            continue
        if b.action == "trash":
            if c["status"] == "trash":
                continue
            db.update(cid, prev_status=c["status"], status="trash")
            db.add_job(cid, "trash", delay=CFG["grace_seconds"])   # undoable like a single delete
        else:
            db.add_job(cid, b.action)
    worker.wake()
    return {"queued": len(b.ids)}


@app.post("/api/prioritize")
def prioritize(b: Ids):
    """The clips the user is looking at get analyzed next."""
    worker.state["priority"] = b.ids[:3]
    worker.wake()
    return {"ok": True}


@app.post("/api/merge")
def merge(b: Ids):
    if len(b.ids) < 2:
        raise HTTPException(400, "need two or more clips")
    db.add_job(b.ids[0], "merge", {"ids": b.ids})
    worker.wake()
    return {"ok": True}


@app.post("/api/trash/empty")
def empty():
    db.add_job(None, "empty_trash")
    worker.wake()
    return {"ok": True}


# ---------- setup: which folder holds the clips ----------

def _count_clips(folder, limit=5000):
    n = 0
    for _, _, files in os.walk(folder):
        n += sum(f.lower().endswith(".mp4") for f in files)
        if n >= limit:
            break
    return n


def _candidates():
    """Folders that probably hold NVIDIA clips: NVIDIA's default (Videos/NVIDIA), then Videos itself."""
    home = Path.home()
    seen, out = set(), []
    for p in [home / "Videos" / "NVIDIA", home / "OneDrive" / "Videos" / "NVIDIA", home / "Videos",
              *(Path(f"{d}:/Videos/NVIDIA") for d in "DEFGH")]:
        try:
            if p.is_dir() and str(p).lower() not in seen:
                seen.add(str(p).lower())
                n = _count_clips(p)
                if n:
                    out.append({"path": str(p), "clips": n})
        except OSError:
            pass
    return out


@app.get("/api/setup")
def setup_info():
    return {"configured": config.configured(), "inbox": CFG["inbox"], "keepers": CFG["keepers"],
            "trash": CFG["trash"], "candidates": _candidates()}


_picker = threading.Lock()


@app.post("/api/setup/browse")
def browse():
    """Opens the Windows folder picker on this PC (the browser cannot hand out real folder paths)."""
    if not _picker.acquire(blocking=False):
        raise HTTPException(409, "the folder picker is already open")
    try:
        import tkinter
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        start = CFG["inbox"] or str(Path.home() / "Videos")
        path = filedialog.askdirectory(parent=root, initialdir=start, title="Choose the folder with your clips")
        root.destroy()
        return {"path": str(Path(path)) if path else None}
    finally:
        _picker.release()


class SetupReq(BaseModel):
    inbox: str


@app.post("/api/setup/check")
def setup_check(r: SetupReq):
    p = Path(r.inbox.strip().strip('"'))
    if not p.is_dir():
        raise HTTPException(400, "That folder does not exist")
    home = p.parent / "Clip Buddy Library"
    return {"path": str(p), "clips": _count_clips(p), "keepers": str(home / "Keepers"), "trash": str(home / "Trash")}


@app.post("/api/setup")
def setup(r: SetupReq):
    info = setup_check(r)
    old = CFG["inbox"]
    config.save(inbox=info["path"])
    if old and os.path.normcase(old) != os.path.normcase(info["path"]):
        # unreviewed clips of the old folder are no longer part of the library; decisions stay
        for c in db.q("SELECT id, path FROM clips WHERE status='new'"):
            if not os.path.normcase(c["path"]).startswith(os.path.normcase(info["path"])):
                db.x("DELETE FROM clips WHERE id=?", (c["id"],))
    worker.state["last_scan"] = 0
    worker.wake()
    return setup_info()


# ---------- files ----------

@app.get("/media/{cid}")
def media(cid: int):
    c = _need(cid)
    if not os.path.exists(c["path"]):
        raise HTTPException(404)
    return FileResponse(c["path"], media_type="video/mp4")


@app.get("/thumb/{cid}/{kind}")
def thumb(cid: int, kind: str):
    if kind not in ("poster", "sprite"):
        raise HTTPException(404)
    f = DATA / "thumbs" / f"{cid}_{kind}.jpg"
    if not f.exists():
        return Response(status_code=404)
    return FileResponse(f, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
