"""Everything that changes files. Rule: a new file is written and checked first; only then does the
old one move to Clip Buddy's Trash folder. Nothing is ever deleted except by 'Empty trash'."""
import os
import re
import shutil
import time
from pathlib import Path

from . import db, encoders, scanner
from .config import CFG, DATA


class ActionError(Exception):
    pass


def _free(path):
    """A path that does not exist yet: adds ' (2)', ' (3)'... before the extension."""
    path = Path(path)
    if not path.exists():
        return path
    for i in range(2, 1000):
        p = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not p.exists():
            return p
    raise ActionError(f"no free name for {path}")


def _ffmpeg(args):
    r = scanner.run(["ffmpeg", "-hide_banner", "-v", "error", "-y", *args])
    if r.returncode:
        raise ActionError(r.stderr.decode(errors="replace")[-600:])


def _copy_times(src, dst):
    st = os.stat(src)
    os.utime(dst, (st.st_atime, st.st_mtime))


def _check_duration(path, expected, tol=1.0):
    d = scanner.probe(path)["duration"]
    if abs(d - expected) > tol:
        raise ActionError(f"output is {d:.1f}s, expected {expected:.1f}s")
    return d


def _vmaf(dist, ref, length, fps):
    """VMAF over up to 10 s from the middle. Frames are matched by index, not timestamp:
    trimmed MP4s carry edit lists that make timestamp matching drift by a frame."""
    fps = fps or 60
    n = int(min(length, 10) * fps)
    a = max(0, int(length * fps / 2) - n // 2)
    pick = f"trim=start_frame={a}:end_frame={a + n},format=yuv420p,setpts=N/({fps}*TB)"
    r = scanner.run(["ffmpeg", "-hide_banner", "-i", str(dist), "-i", str(ref), "-lavfi",
                     f"[0:v]{pick}[a];[1:v]{pick}[b];[a][b]libvmaf=n_subsample=2:n_threads=8",
                     "-fps_mode", "passthrough", "-f", "null", "-"])
    m = re.search(rb"VMAF score: ([\d.]+)", r.stderr)
    if not m:
        raise ActionError("quality check failed to run")
    return float(m.group(1))


# ---------- trash ----------

def to_trash(path, note=None, status_from="new", clip_id=None):
    """Move a file into the Trash folder and record it. Returns the trash row id."""
    src = Path(path)
    dst = _free(Path(CFG["trash"]) / src.name)
    shutil.move(str(src), str(dst))
    if clip_id:
        db.update(clip_id, path=str(dst), prev_path=str(src), prev_status=status_from,
                  status="trash", trashed_at=time.time(), note=note)
        return clip_id
    meta = scanner.probe(dst)
    game, kind, start, _ = scanner.parse_name(src, meta["duration"])
    return db.x("INSERT INTO clips(path, game, kind, start_ts, size, mtime, duration, vcodec, status, "
                "prev_path, prev_status, trashed_at, note, analyzed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                (str(dst), game, kind, start, dst.stat().st_size, dst.stat().st_mtime, meta["duration"],
                 meta["vcodec"], "trash", str(src), status_from, time.time(), note))


def trash(cid):
    c = db.clip(cid)
    to_trash(c["path"], note="deleted", status_from=c["prev_status"] or "new", clip_id=cid)


def restore(cid):
    c = db.clip(cid)
    if c["status"] != "trash":
        return
    dst = _free(c["prev_path"])
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(c["path"], str(dst))
    back = c["prev_status"] if c["prev_status"] in ("keep", "temp") else "new"
    db.update(cid, path=str(dst), status=back, prev_path=None, trashed_at=None, note=None,
              keep_start=None, keep_end=None)


def empty_trash():
    freed = 0
    for c in db.q("SELECT id, path, size FROM clips WHERE status='trash' AND busy=0"):
        try:
            os.remove(c["path"])
        except FileNotFoundError:
            pass
        freed += c["size"] or 0
        db.x("DELETE FROM clips WHERE id=?", (c["id"],))
        for f in (DATA / "thumbs").glob(f"{c['id']}_*"):
            f.unlink()
    db.bump_stat("freed_bytes", freed)
    return freed


# ---------- keep ----------

def _keeper_path(c, src):
    return _free(Path(CFG["keepers"]) / c["game"] / Path(src).name)


def trim(cid, start, end):
    """Lossless cut (no re-encode). Start snaps back to the keyframe before it (0.5 s here)."""
    c = db.clip(cid)
    src = Path(c["path"])
    tmp = src.with_name(src.stem + ".cb-trim.mp4")
    _ffmpeg(["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src), "-map", "0", "-c", "copy",
             "-avoid_negative_ts", "make_zero", "-movflags", "+faststart", str(tmp)])
    _check_duration(tmp, end - start, tol=1.5)
    _copy_times(src, tmp)
    to_trash(src, note="original, replaced by the trimmed version", status_from="keep")
    final = src  # same name and place as before
    os.replace(tmp, final)
    db.update(cid, trimmed=1, orig_size=c["orig_size"] or c["size"], keep_start=None, keep_end=None)
    scanner.index_file(final)


def compress(cid):
    c = db.clip(cid)
    if c["vcodec"] != "h264":
        return  # already HEVC/AV1: re-encoding would only lose quality
    src = Path(c["path"])
    tmp = src.with_name(src.stem + ".cb-hevc.mp4")
    _ffmpeg(["-i", str(src), "-map", "0", *encoders.args(),
             "-tag:v", "hvc1", "-c:a", "copy", "-movflags", "+faststart", str(tmp)])
    try:
        _check_duration(tmp, c["duration"])
        score = _vmaf(tmp, src, c["duration"], c["fps"])
        if score < CFG["vmaf_min"]:
            raise ActionError(f"quality {score:.1f} below {CFG['vmaf_min']}; original kept")
        if tmp.stat().st_size >= src.stat().st_size * 0.9:
            raise ActionError("compressed file is not meaningfully smaller; original kept")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    _copy_times(src, tmp)
    to_trash(src, note=f"original, replaced by the compressed version (quality {score:.1f}/100)", status_from="keep")
    os.replace(tmp, src)
    db.update(cid, compressed=1, orig_size=c["orig_size"] or c["size"])
    scanner.index_file(src)
    return score


def keep(cid, start=None, end=None, do_compress=None):
    """Move to Keepers/<Game>/, trim to [start, end] if given, then compress."""
    c = db.clip(cid)
    src = Path(c["path"])
    if Path(CFG["keepers"]) not in src.parents:
        dst = _keeper_path(c, src)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        db.update(cid, path=str(dst))
    if start is not None and end is not None and (start > 0.6 or end < c["duration"] - 0.6):
        trim(cid, start, end)
    if CFG["compress_keepers"] if do_compress is None else do_compress:
        try:
            compress(cid)
        except ActionError as e:
            raise ActionError(f"kept, but not compressed: {e}") from e


def rename(cid, title):
    """'PEAK 2026.01.18 - 20.48.30.06.DVR.mp4' + '1v3 clutch' -> 'PEAK 2026.01.18 - 20.48.30.06.DVR - 1v3 clutch.mp4'."""
    c = db.clip(cid)
    src = Path(c["path"])
    m = re.match(r"^.+? \d{4}\.\d\d\.\d\d - \d\d\.\d\d\.\d\d(?:\.\d\d)?(?:\.DVR)?", src.name)
    base = m.group(0) if m else src.stem.split(" - ")[0]
    clean = re.sub(r'[<>:"/\\|?*]', "", title or "").strip()
    name = f"{base} - {clean}.mp4" if clean else f"{base}.mp4"
    if name == src.name:
        return
    dst = _free(src.with_name(name))
    os.rename(src, dst)
    db.update(cid, path=str(dst), title=clean or None)


# ---------- continuations ----------
# NVIDIA clears the replay buffer after a save, so pressing the hotkey again soon after gives a clip
# that starts where the previous one ended. Such runs are joined losslessly into one clip.

def _gap(a, b):
    return b["start_ts"] - (a["start_ts"] + a["duration"])


def chains():
    """Runs of same-game clips where each starts within 3 s of the previous one's end."""
    rows = db.q("SELECT * FROM clips WHERE status IN ('new','temp','keep') AND busy=0 AND trimmed=0 "
                "ORDER BY game, start_ts")
    out, cur = [], []
    for c in rows:
        if cur and cur[-1]["game"] == c["game"] and abs(_gap(cur[-1], c)) < 3:
            cur.append(c)
            continue
        if len(cur) > 1:
            out.append(cur)
        cur = [c]
    if len(cur) > 1:
        out.append(cur)
    return out


def merge(ids):
    clips = sorted((db.clip(i) for i in ids), key=lambda c: c["start_ts"])
    for a, b in zip(clips, clips[1:]):
        if a["game"] != b["game"] or abs(_gap(a, b)) >= 3:
            raise ActionError(f"#{b['id']} does not continue #{a['id']}")
    last = Path(clips[-1]["path"])
    lst = last.parent / ".cb-concat.txt"
    out = last.parent / ".cb-merged.mp4"
    lst.write_text("".join(f"file '{Path(c['path']).as_posix()}'\n" for c in clips), encoding="utf-8")
    try:
        _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-map", "0", "-c", "copy",
                 "-movflags", "+faststart", str(out)])
        _check_duration(out, sum(c["duration"] for c in clips), tol=1.5)
    except Exception:
        out.unlink(missing_ok=True)
        raise
    finally:
        lst.unlink(missing_ok=True)
    first = clips[0]
    final = _free(Path(first["path"]).with_name(Path(first["path"]).stem + f" (joined {len(clips)}).mp4"))
    _copy_times(last, out)
    os.replace(out, final)
    status = "keep" if any(c["status"] == "keep" for c in clips) else "new"
    for c in clips:
        to_trash(c["path"], note=f"joined into {final.name}", status_from=c["status"], clip_id=c["id"])
    nid = scanner.index_file(final)
    # the file is named after the first clip but contains all of them: fix kind/start from the parts
    db.update(nid, kind=clips[-1]["kind"], start_ts=first["start_ts"], status=status,
              rating=max((c["rating"] or 0) for c in clips) or None,
              tags=",".join(sorted({t for c in clips for t in (c["tags"] or "").split(",") if t})) or None)
    return nid
