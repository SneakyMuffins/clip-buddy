"""Finds clips in the inbox and keepers folders and keeps the clips table in sync with disk."""
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import db
from .config import CFG, ROOT

# "<Game> 2026.01.18 - 20.48.30.06.DVR.mp4", optionally " - <title>" before .mp4 (our renames)
NAME_RE = re.compile(
    r"^(?P<game>.+?) (?P<date>\d{4}\.\d\d\.\d\d) - (?P<time>\d\d\.\d\d\.\d\d)(?:\.\d\d)?"
    r"(?P<dvr>\.DVR)?(?: \((?:joined )?\d+\))?(?: - (?P<title>.+?))?\.mp4$", re.I)

NOWIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
# ffmpeg/ffprobe: the copy bundled in "ffmpeg\" next to the app (the Windows download) first, then PATH.
TOOLS = {n: str(ROOT / "ffmpeg" / f"{n}.exe") if (ROOT / "ffmpeg" / f"{n}.exe").exists() else n
         for n in ("ffmpeg", "ffprobe")}


def run(cmd, **kw):
    cmd = [TOOLS.get(cmd[0], cmd[0]), *cmd[1:]]
    return subprocess.run(cmd, capture_output=True, creationflags=NOWIN, **kw)


def probe(path):
    r = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    info = json.loads(r.stdout or b"{}")
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    num, _, den = v.get("r_frame_rate", "0/1").partition("/")
    fmt = info.get("format", {})
    return {
        "duration": float(fmt.get("duration") or 0),
        "bitrate": int(fmt.get("bit_rate") or 0),
        "width": v.get("width"), "height": v.get("height"),
        "fps": round(float(num) / float(den or 1), 2) if num else None,
        "vcodec": v.get("codec_name"),
    }


def parse_name(path, duration):
    """Game, kind and clip start time. Replay file names carry the save (= end) time,
    manual recordings carry the start time."""
    p = Path(path)
    m = NAME_RE.match(p.name)
    if m:
        t = datetime.strptime(m["date"] + " " + m["time"], "%Y.%m.%d %H.%M.%S").timestamp()
        kind = "replay" if m["dvr"] else "recording"
        start = t - duration if kind == "replay" else t
        return m["game"], kind, start, m["title"]
    mt = p.stat().st_mtime
    game = p.parent.name if p.parent.name.lower() not in ("keepers", "nvidia") else "Other"
    return game, "recording", mt - duration, None


def roots():
    return [Path(CFG["inbox"]), Path(CFG["keepers"])] if CFG["inbox"] else []


def walk():
    for root in roots():
        if not root.exists():
            continue
        for dirpath, dirs, files in os.walk(root):
            # never walk into the app's own library (e.g. after choosing the parent folder as the clips folder)
            dirs[:] = [d for d in dirs if d != "Clip Buddy Library"
                       and not (CFG["trash"] and os.path.normcase(os.path.join(dirpath, d)) == os.path.normcase(CFG["trash"]))]
            for f in files:
                if f.lower().endswith(".mp4") and ".cb-" not in f:
                    yield Path(dirpath) / f


def index_file(path, **extra):
    """Probe one file and insert/refresh its row. Returns the clip id."""
    st = path.stat()
    meta = probe(path)
    game, kind, start, title = parse_name(path, meta["duration"])
    row = db.one("SELECT id, title FROM clips WHERE path=?", (str(path),))
    fields = dict(game=game, kind=kind, start_ts=start, size=st.st_size, mtime=st.st_mtime, **meta)
    if row:
        db.update(row["id"], **{"analyzed": 0, **fields, **extra})
        return row["id"]
    fields.update(path=str(path), title=title, **extra)
    cols = ", ".join(fields)
    return db.x(f"INSERT INTO clips({cols}) VALUES ({', '.join('?' * len(fields))})", tuple(fields.values()))


_unreadable = set()   # (path, size, mtime) that ffprobe failed on; retried only when the file changes
_queue = []           # files found by the last folder walk that still need indexing: [(path, stat)]
stats = {"pending_bytes": 0, "index_seconds": 0.0, "indexed": 0}   # for the progress estimate


def _walk_pending():
    """One pass over the folders: drop rows whose file is gone, return new/changed files."""
    known = {r["path"]: r for r in db.q("SELECT id, path, size, mtime, busy FROM clips")}
    seen, pending = set(), []
    for path in walk():
        sp = str(path)
        seen.add(sp)
        try:
            st = path.stat()
        except OSError:
            continue
        r = known.get(sp)
        if r and r["size"] == st.st_size and abs((r["mtime"] or 0) - st.st_mtime) < 1:
            continue
        if r and r["busy"]:
            continue
        # NVIDIA may still be writing a fresh file; wait until it is a few seconds old
        if not r and st.st_mtime > time.time() - 5:
            continue
        if (sp, st.st_size, st.st_mtime) in _unreadable:
            continue
        pending.append((path, st))
    # The library folders are the truth: files in Trash or Keepers that have no record (for example after
    # running a second copy of the app, which has its own records) are taken in as trashed / kept.
    trash = Path(CFG["trash"]) if CFG["trash"] else None
    if trash and trash.is_dir():
        for f in trash.iterdir():
            if f.suffix.lower() == ".mp4" and ".cb-" not in f.name and str(f) not in known:
                try:
                    pending.append((f, f.stat()))
                except OSError:
                    pass
    for sp, r in known.items():
        if sp not in seen and not r["busy"] and not os.path.exists(sp):
            db.x("DELETE FROM clips WHERE id=?", (r["id"],))
    return pending


def _inside(path, folder):
    return bool(folder) and os.path.normcase(str(Path(path).resolve())).startswith(
        os.path.normcase(str(Path(folder).resolve())) + os.sep)


def scan(max_new=None):
    """Add new/changed files, drop rows whose file is gone. Cheap when nothing changed.
    The folder is walked once; with max_new, at most that many files are indexed per call, so analysis
    can run in between. Returns how many files are still waiting to be indexed."""
    global _queue
    if not _queue:
        _queue = _walk_pending()
    batch = _queue if max_new is None else _queue[:max_new]
    _queue = [] if max_new is None else _queue[max_new:]
    t = time.time()
    for path, st in batch:
        try:
            if not path.exists():
                continue
            new = not db.one("SELECT id FROM clips WHERE path=?", (str(path),))
            if new and _inside(path, CFG["trash"]):
                index_file(path, status="trash", trashed_at=st.st_mtime, note="found in the Trash folder", analyzed=1)
            elif new and _inside(path, CFG["keepers"]):
                index_file(path, status="keep")
            else:
                index_file(path)
        except Exception:
            _unreadable.add((str(path), st.st_size, st.st_mtime))
    stats["index_seconds"] += time.time() - t
    stats["indexed"] += len(batch)
    stats["pending_bytes"] = sum(st.st_size for _, st in _queue)
    return len(_queue)
