"""Everything that changes files. Rules:
- A new file is written and checked first; only then does the old one move to Clip Buddy's Trash folder.
- Every move goes through _move, which never leaves a half copy behind, and is written to data/history.jsonl.
- Every action can run again after the app was closed half way: it looks where the file really is first.
- Nothing is ever deleted except by 'Empty trash', and that deletes only what you deleted."""
import errno
import json
import os
import re
import shutil
import time
from itertools import accumulate
from pathlib import Path

from . import db, encoders, scanner
from .config import CFG, DATA

HISTORY = DATA / "history.jsonl"


class ActionError(Exception):
    pass


def log(event, **info):
    """Append-only record of every file change, so 'where did this clip go?' always has an answer."""
    try:
        with open(HISTORY, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(time.time(), 1), "event": event, **info}, ensure_ascii=False) + "\n")
    except OSError:
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


def _move(src, dst, why):
    """Move a file. On one drive it is a single rename: all or nothing, and it never overwrites. A file open
    in another program (a player, Explorer's preview, this app's own video stream) is retried for a few
    seconds, then the move fails with nothing changed. Across drives: copy, then delete the source; if that
    delete fails the copy is removed again, so there is never a second, unrecorded copy."""
    src, dst = str(src), str(dst)
    for attempt in range(6):
        try:
            os.rename(src, dst)
            break
        except OSError as e:
            if getattr(e, "winerror", None) == 17 or e.errno == errno.EXDEV:   # another drive
                shutil.copy2(src, dst)
                try:
                    os.remove(src)
                except OSError:
                    os.remove(dst)
                    raise
                break
            if getattr(e, "winerror", None) in (5, 32, 33) and attempt < 5:   # open elsewhere: wait a moment
                time.sleep(2)
                continue
            raise
    log("move", src=src, dst=dst, why=why)


def _find_moved(c, folder):
    """After a move that was cut off before it was recorded: the clip's file in folder, under its own name
    or a ' (2)' one, with the same size and no record of its own."""
    name = Path(c["path"]).name
    stem, ext = os.path.splitext(name)
    for i in range(1, 20):
        p = Path(folder) / (name if i == 1 else f"{stem} ({i}){ext}")
        try:
            if p.stat().st_size == c["size"] and not db.one("SELECT id FROM clips WHERE path=?", (str(p),)):
                return p
        except OSError:
            pass
    return None


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


def _tmp(src, kind):
    return src.with_name(f"{src.stem}.cb-{kind}{src.suffix}")


def _mux_args(path):
    """MP4/MOV only: index at the front (plays instantly) and the HEVC tag players expect."""
    return ["-movflags", "+faststart"] if Path(path).suffix.lower() in (".mp4", ".mov") else []


def _vmaf(dist, ref, length, fps):
    """VMAF over up to 10 s from the middle. Frames are matched by index, not timestamp:
    trimmed MP4s carry edit lists that make timestamp matching drift by a frame. fps must be the real
    average rate: variable-frame-rate captures have far fewer frames than their nominal rate suggests."""
    fps = fps or 60
    n = max(1, int(min(length, 10) * fps))
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
    Path(CFG["trash"]).mkdir(parents=True, exist_ok=True)
    dst = _free(Path(CFG["trash"]) / src.name)
    if not clip_id:
        # read everything before the move: the file's own name, folder and times are gone afterwards
        meta = scanner.probe(src)
        st = src.stat()
        game, kind, start, _ = scanner.parse_name(src, meta["duration"], st.st_mtime)
    _move(src, dst, note or "to trash")
    try:
        if clip_id:
            db.update(clip_id, path=str(dst), prev_path=str(src), prev_status=status_from,
                      status="trash", trashed_at=time.time(), note=note)
            return clip_id
        return db.x("INSERT INTO clips(path, game, kind, start_ts, size, mtime, duration, vcodec, status, "
                    "prev_path, prev_status, trashed_at, note, analyzed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
                    (str(dst), game, kind, start, st.st_size, st.st_mtime, meta["duration"],
                     meta["vcodec"], "trash", str(src), status_from, time.time(), note))
    except Exception:
        _move(dst, src, "put back: it could not be recorded")   # never leave an unknown file in Trash
        raise


def _swap(src, new, note):
    """Put a checked new file (trimmed/compressed) in place of a kept clip; the original goes to Trash.
    If any step fails, the original is put back where it was."""
    tid = to_trash(src, note=note, status_from="keep")
    try:
        os.replace(new, src)
    except Exception:
        # if even this fails, the original stays in Trash recorded as this clip's original: Empty keeps it
        _move(db.clip(tid)["path"], src, "put back: the new version could not take its place")
        db.x("DELETE FROM clips WHERE id=?", (tid,))
        Path(new).unlink(missing_ok=True)
        raise
    log("replace", path=str(src), original=db.clip(tid)["path"], why=note)


def trash(cid):
    c = db.clip(cid)
    if not c or c["status"] != "trash" or c["trashed_at"]:
        return   # the decision was undone or changed before it ran, or it already ran
    src = Path(c["path"])
    moved = None if src.exists() else _find_moved(c, CFG["trash"])
    if moved:   # cut off last time between the move and its record
        db.update(cid, path=str(moved), prev_path=str(src), status="trash", trashed_at=time.time(), note="deleted")
        return
    try:
        to_trash(src, note="deleted", status_from=c["prev_status"] or "new", clip_id=cid)
    except Exception:
        db.update(cid, status=c["prev_status"] or "new")   # nothing moved: back where it was, the error says why
        raise


def restore(cid):
    c = db.clip(cid)
    if not c or c["status"] != "trash":
        return
    if not c["trashed_at"]:
        if not scanner._inside(c["path"], CFG["trash"]):   # a delete that never moved the file: just undo it
            db.update(cid, status=c["prev_status"] or "new")
        return
    # a file found in the Trash folder without a record has no known origin: back to the clips folder
    origin = Path(c["prev_path"] or Path(CFG["inbox"]) / (c["game"] or "Restored") / Path(c["path"]).name)
    src = Path(c["path"])
    if src.exists():
        dst = _free(origin)
        dst.parent.mkdir(parents=True, exist_ok=True)
        _move(src, dst, "restore")
    else:
        dst = _find_moved(c, origin.parent)   # cut off last time between the move and its record
        if not dst:
            raise ActionError("the file is no longer in the Trash folder")
    back = c["prev_status"] if c["prev_status"] in ("keep", "temp") else "new"
    fields = dict(path=str(dst), status=back, prev_path=None, trashed_at=None, note=None,
                  keep_start=None, keep_end=None)
    if not (DATA / "analysis" / f"{cid}.json").exists():
        fields["analyzed"] = 0   # e.g. an original saved before a trim: never analyzed, no thumbnails yet
    db.update(cid, **fields)


def protected(c):
    """Why Empty trash must keep this Trash file, or None. It keeps the only copy of anything you did not
    delete yourself:
    - "unknown": no record of where it came from (found in the Trash folder: a second copy of the app, or an
      older version's failed keep). It may be a kept clip; it can be restored, or deleted on its own.
    - "needed": the original of a kept clip whose new version is missing."""
    if not c["prev_path"]:
        return "unknown"
    if (c["note"] or "").startswith("original, replaced") and not os.path.exists(c["prev_path"]):
        # unless you then deleted or joined that kept clip yourself: its own record points at the same path
        if not db.one("SELECT id FROM clips WHERE status='trash' AND prev_path=? AND id!=? "
                      "AND (note='deleted' OR note LIKE 'joined into %')", (c["prev_path"], c["id"])):
            return "needed"
    return None


def empty_trash(unknown=False):
    """Deletes what is really in the Trash folder and was deleted by you (or replaced by a kept version).
    A clip still in its undo window is not in the folder yet and is left alone; files of unknown origin go
    only with unknown=True (a separate confirmation); a locked file is skipped and reported, not fatal."""
    folders = [f for f in (CFG["trash"], *CFG.get("old_trash", [])) if f]
    freed, locked = 0, []
    for c in db.q("SELECT id, path, size, prev_path, note FROM clips "
                  "WHERE status='trash' AND busy=0 AND trashed_at IS NOT NULL"):
        if not any(scanner._inside(c["path"], f) for f in folders):
            continue
        why = protected(c)
        if why == "needed" or (why == "unknown" and not unknown):
            continue
        try:
            os.remove(c["path"])
        except FileNotFoundError:
            pass
        except OSError:
            locked.append(Path(c["path"]).name)
            continue
        log("delete", path=c["path"], size=c["size"], why=c["note"])
        freed += c["size"] or 0
        db.x("DELETE FROM clips WHERE id=?", (c["id"],))
        for f in (DATA / "thumbs").glob(f"{c['id']}_*"):
            f.unlink(missing_ok=True)
        (DATA / "analysis" / f"{c['id']}.json").unlink(missing_ok=True)
    db.bump_stat("freed_bytes", freed)
    if locked:
        raise ActionError(f"freed {freed / 1e9:.1f} GB; {len(locked)} file(s) are open in another program and were "
                          f"kept: {', '.join(locked[:3])}{' ...' if len(locked) > 3 else ''}")
    return freed


# ---------- keep ----------

def _keyframe_before(src, t):
    """The keyframe a lossless cut at t really starts from: 0.5 s back at most on NVIDIA files, several
    seconds on some other tools' files and on HEVC."""
    r = scanner.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-skip_frame", "nokey",
                     "-show_entries", "frame=pts_time", "-read_intervals", f"{max(0.0, t - 30):.3f}%{t + 0.01:.3f}",
                     "-of", "csv=p=0", str(src)])
    times = []
    for line in r.stdout.decode(errors="replace").split():
        try:
            times.append(float(line.strip(",")))
        except ValueError:
            pass
    before = [x for x in times if x <= t + 0.001]
    return max(before) if before else 0.0


def trim(cid, start, end):
    """Lossless cut (no re-encode). The start snaps back to the keyframe before it."""
    c = db.clip(cid)
    src = Path(c["path"])
    tmp = _tmp(src, "trim")
    kf = _keyframe_before(src, start)
    try:
        _ffmpeg(["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src), "-map", "0", "-c", "copy",
                 "-avoid_negative_ts", "make_zero", *_mux_args(src), str(tmp)])
        _check_duration(tmp, end - kf, tol=1.0)
        _copy_times(src, tmp)
        _swap(src, tmp, "original, replaced by the trimmed version")   # same name and place as before
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    db.update(cid, trimmed=1, orig_size=c["orig_size"] or c["size"], keep_start=None, keep_end=None)
    scanner.index_file(src)


def compress(cid):
    c = db.clip(cid)
    if not c or c["status"] == "trash":
        return
    src = Path(c["path"])
    info = scanner.probe(src)   # the file as it is now, not as recorded: a run cut off after its swap left HEVC
    if info["vcodec"] != "h264":
        orig = db.one("SELECT size FROM clips WHERE status='trash' AND prev_path=? "
                      "AND note LIKE 'original, replaced by the compressed%'", (str(src),))
        if orig and not c["compressed"]:
            db.update(cid, compressed=1, orig_size=c["orig_size"] or orig["size"])
            scanner.index_file(src)
        elif not orig:
            db.update(cid, note=f"Not compressed: it is already {(info['vcodec'] or 'compact').upper()}, "
                                f"so re-encoding would only lose quality")
        return None
    fps = next((s["avg_fps"] for s in scanner.streams(src) if s["codec_type"] == "video"), None) or c["fps"]
    tmp = _tmp(src, "hevc")
    tag = ["-tag:v", "hvc1"] if src.suffix.lower() in (".mp4", ".mov") else []
    scores = []
    try:
        # Detailed footage can miss the quality bar at the default strength (measured: one racing clip scored
        # 94.8 at cq 28). Then try once more a step gentler (~3x smaller instead of ~4.5x) before giving up.
        for cq in (CFG["cq"], CFG["cq"] - 4):
            _ffmpeg(["-i", str(src), "-map", "0", *encoders.args(cq), *tag, "-c:a", "copy", *_mux_args(src), str(tmp)])
            _check_duration(tmp, info["duration"])
            score = _vmaf(tmp, src, info["duration"], fps)
            scores.append(f"{score:.1f}")
            if score >= CFG["vmaf_min"]:
                break
        else:
            # not an error: the clip is kept as it is, at full quality. Recorded on the clip, not retried.
            tmp.unlink(missing_ok=True)
            db.update(cid, note=f"Not compressed: quality {' then '.join(scores)} was below {CFG['vmaf_min']}, "
                                f"so the original quality was kept")
            return None
        if tmp.stat().st_size >= src.stat().st_size * 0.9:
            # e.g. older recordings made at a low bitrate: already compact, HEVC barely helps. Not an error.
            saved = 1 - tmp.stat().st_size / src.stat().st_size
            tmp.unlink(missing_ok=True)
            db.update(cid, note=f"Not compressed: it is already compact (HEVC would save only {max(saved, 0):.0%})")
            return None
        _copy_times(src, tmp)
        _swap(src, tmp, f"original, replaced by the compressed version (quality {score:.1f}/100)")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    db.update(cid, compressed=1, orig_size=c["orig_size"] or c["size"])
    scanner.index_file(src)
    return score


def keep(cid, start=None, end=None, do_compress=None):
    """Move to Keepers/<Game>/, trim to [start, end] if given, then compress."""
    c = db.clip(cid)
    if not c or c["status"] != "keep" or scanner._inside(c["path"], CFG["trash"]):
        return   # the decision was changed (e.g. deleted in a bulk action) before this ran
    src = Path(c["path"])
    if not scanner._inside(src, CFG["keepers"]):
        folder = Path(CFG["keepers"]) / c["game"]
        moved = None if src.exists() else _find_moved(c, folder)
        if moved:
            src = moved   # cut off last time right after the move
        else:
            dst = _free(folder / src.name)
            dst.parent.mkdir(parents=True, exist_ok=True)
            _move(src, dst, "keep")
            src = dst
        db.update(cid, path=str(src))
    ranged = start is not None and end is not None and (start > 0.6 or end < c["duration"] - 0.6)
    # a keep cut off after its trim runs again at the next start: the Trash says the cut was made
    cut = db.one("SELECT id FROM clips WHERE status='trash' AND prev_path=? "
                 "AND note LIKE 'original, replaced by the trimmed%'", (str(src),))
    if ranged and not cut:
        trim(cid, start, end)
    if CFG["compress_keepers"] if do_compress is None else do_compress:
        try:
            compress(cid)
        except ActionError as e:
            raise ActionError(f"kept, but not compressed: {e}") from e


def rename(cid, title):
    """'<name>.mp4' + '1v3 clutch' -> '<name> - 1v3 clutch.mp4'. A new title replaces the old one."""
    c = db.clip(cid)
    if not c:
        return
    src = Path(c["path"])
    clean = re.sub(r'[<>:"/\\|?*]', "", title or "").strip()
    stem = src.stem
    if c["title"] and stem.endswith(f" - {c['title']}"):
        stem = stem[:-len(f" - {c['title']}")]
    name = f"{stem} - {clean}{src.suffix}" if clean else f"{stem}{src.suffix}"
    if name != src.name:
        dst = src.with_name(name)
        if src.exists() or not dst.exists() or db.one("SELECT id FROM clips WHERE path=?", (str(dst),)):
            dst = _free(dst)
            _move(src, dst, "rename")   # (a re-run after a cut-off rename finds the file already at dst)
        db.update(cid, path=str(dst))
        db.x("UPDATE clips SET prev_path=? WHERE status='trash' AND prev_path=?", (str(dst), str(src)))
    db.update(cid, title=clean or None)


# ---------- continuations ----------
# NVIDIA clears the replay buffer after a save, so pressing the hotkey again soon after gives a clip
# that starts where the previous one ended. Such runs are joined losslessly into one clip.

def _gap(a, b):
    return b["start_ts"] - (a["start_ts"] + a["duration"])


def chains():
    """Runs of same-game, same-format clips where each starts within 3 s of the previous one's end."""
    rows = db.q("SELECT * FROM clips WHERE status IN ('new','temp','keep') AND busy=0 AND trimmed=0 AND compressed=0 "
                "ORDER BY game, start_ts")
    out, cur = [], []
    same = ("game", "vcodec", "width", "height", "fps")
    for c in rows:
        if cur and all(cur[-1][k] == c[k] for k in same) and abs(_gap(cur[-1], c)) < 3:
            cur.append(c)
            continue
        if len(cur) > 1:
            out.append(cur)
        cur = [c]
    if len(cur) > 1:
        out.append(cur)
    return out


def _layout(path):
    return [{k: v for k, v in s.items() if k not in ("duration", "avg_fps")} for s in scanner.streams(path)]


def _plays(path, points):
    """True if the file decodes without errors around each point where two clips meet."""
    for t in points:
        r = scanner.run(["ffmpeg", "-hide_banner", "-v", "error", "-xerror", "-ss", f"{max(0.0, t - 2):.3f}",
                         "-t", "4", "-i", str(path), "-f", "null", "-"])
        if r.returncode or r.stderr.strip():
            return False
    return True


def _joined_row(final, clips):
    nid = (db.one("SELECT id FROM clips WHERE path=?", (str(final),)) or {}).get("id") or scanner.index_file(final)
    status = "keep" if any((c["prev_status"] if c["status"] == "trash" else c["status"]) == "keep" for c in clips) else "new"
    # the file is named after the first clip but contains all of them: fix kind/start from the parts
    db.update(nid, kind=clips[-1]["kind"], start_ts=clips[0]["start_ts"], status=status,
              rating=max((c["rating"] or 0) for c in clips) or None,
              tags=",".join(sorted({t for c in clips for t in (c["tags"] or "").split(",") if t})) or None)
    if status == "keep":
        db.add_job(nid, "keep")   # into Keepers and compressed, like any kept clip
    return nid


def merge(ids):
    clips = [db.clip(i) for i in ids]
    if any(c is None for c in clips):
        raise ActionError("a clip in this join is no longer in the library")
    clips.sort(key=lambda c: c["start_ts"])
    first = clips[0]
    # a join cut off last time while its parts were moving to Trash: finish that one instead of joining again
    done = [c for c in clips if c["status"] == "trash" and (c["note"] or "").startswith("joined into ")]
    if done:
        home = Path(first["prev_path"] if first["status"] == "trash" else first["path"]).parent
        final = home / done[0]["note"][len("joined into "):]
        if not final.exists():
            raise ActionError("an earlier join of these clips was cut off and its file is missing; "
                              "restore the parts from Trash")
        for c in clips:
            if c not in done:
                to_trash(c["path"], note=f"joined into {final.name}", status_from=c["status"], clip_id=c["id"])
        return _joined_row(final, [db.clip(c["id"]) for c in clips])
    for c in clips:
        if c["status"] == "trash":
            raise ActionError(f"{Path(c['path']).name} is in the Trash")
    for a, b in zip(clips, clips[1:]):
        if a["game"] != b["game"] or abs(_gap(a, b)) >= 3:
            raise ActionError(f"#{b['id']} does not continue #{a['id']}")
    if any(c["compressed"] or c["trimmed"] for c in clips):
        raise ActionError("one of these clips was already trimmed or compressed, so it no longer continues "
                          "the next one exactly")
    layouts = [_layout(c["path"]) for c in clips]
    if not layouts[0] or any(lay != layouts[0] for lay in layouts[1:]):
        raise ActionError("these clips were saved in different formats, so they can't be joined without re-encoding")
    ext = Path(first["path"]).suffix
    total = sum(c["duration"] for c in clips)
    # cut off last time after the joined file was in place but before any part moved: use that file
    made = Path(first["path"]).with_name(Path(first["path"]).stem + f" (joined {len(clips)}){ext}")
    if made.exists() and not db.one("SELECT id FROM clips WHERE path=?", (str(made),))             and abs(scanner.probe(made)["duration"] - total) <= 1.5 and _layout(made) == layouts[0]:
        final = made
    else:
        final = _join(clips, layouts[0], made)
    log("join", parts=[c["path"] for c in clips], into=str(final))
    for c in clips:
        to_trash(c["path"], note=f"joined into {final.name}", status_from=c["status"], clip_id=c["id"])
    return _joined_row(final, [db.clip(c["id"]) for c in clips])


def _join(clips, layout, name):
    """Concat the clips losslessly and check the result before anything else changes."""
    ext = name.suffix
    last = Path(clips[-1]["path"])
    lst = last.parent / ".cb-concat.txt"
    out = last.parent / f".cb-merged{ext}"

    def quoted(p):   # ffmpeg's concat list: ' inside a quoted name is written '\''
        return Path(p).as_posix().replace("'", "'\\''")
    lst.write_text("".join(f"file '{quoted(c['path'])}'\n" for c in clips), encoding="utf-8")
    lengths = [c["duration"] for c in clips]
    total = sum(lengths)
    try:
        _ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-map", "0", "-c", "copy", *_mux_args(out), str(out)])
        _check_duration(out, total, tol=1.5)
        tracks = scanner.streams(out)
        for s in tracks:   # every track, not just the container: audio can drop out of a join
            if s["duration"] and abs(s["duration"] - total) > 1.5:
                raise ActionError(f"the joined {s['codec_type']} is {s['duration']:.1f}s, expected {total:.1f}s")
        if len(tracks) != len(layout):
            raise ActionError("the joined file lost a track")
        if not _plays(out, list(accumulate(lengths))[:-1]):
            raise ActionError("the joined file does not play cleanly where the clips meet")
        final = _free(name)
        _copy_times(last, out)
        os.replace(out, final)
    except Exception:
        out.unlink(missing_ok=True)
        raise
    finally:
        lst.unlink(missing_ok=True)
    return final
