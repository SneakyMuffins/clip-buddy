"""Finds the moment a clip was saved for, and makes its thumbnails.

Signals, per 0.5 s bin:
  loud   - audio level (dB), compared to this clip's own typical level
  voice  - level in the 300-3000 Hz speech band (shouting, laughing)
  motion - how much the picture changes between keyframes (crashes, kill cams, cuts)
For Instant Replay files the hotkey was pressed at the end, so later seconds get more weight.
"""
import json

import numpy as np
from PIL import Image

from . import db, scanner
from .config import DATA

SR = 16000          # audio sample rate used for analysis
HOP = 0.5           # seconds per score bin
MAX_FRAMES = 400    # cap on decoded keyframes for long recordings
TW, TH = 320, 180   # thumbnail size
QUIET = 0.85        # peak activity below this = "nothing much happens" (calibrated on ~100 real clips: flags ~5%)


def _audio(path):
    r = scanner.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR),
                     "-f", "f32le", "pipe:1"])
    return np.frombuffer(r.stdout, dtype=np.float32)


def _frames(path, duration):
    """Keyframes only (cheap to decode), downscaled. Returns (frames, times)."""
    vf = f"scale={TW}:{TH}"
    if duration * 2 > MAX_FRAMES:
        vf = f"fps={MAX_FRAMES / duration:.4f}," + vf
    r = scanner.run(["ffmpeg", "-v", "error", "-skip_frame", "nokey", "-i", str(path), "-an",
                     "-vf", vf, "-fps_mode", "passthrough", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"])
    n = len(r.stdout) // (TW * TH * 3)
    frames = np.frombuffer(r.stdout[: n * TW * TH * 3], dtype=np.uint8).reshape(n, TH, TW, 3)
    times = (np.arange(n) + 0.5) * (duration / max(n, 1)) if n else np.zeros(0)
    return frames, times


def _db(x):
    return 20 * np.log10(np.maximum(x, 1e-6))


def _rz(x, floor):
    """Robust z-score: how far above its own median, in units of its spread. Negative clipped."""
    med = np.median(x)
    mad = np.median(np.abs(x - med)) * 1.4826
    return np.clip((x - med) / max(mad, floor), 0, 6)


def _smooth(x, n):
    if len(x) < n:
        return x
    return np.convolve(x, np.ones(n) / n, mode="same")


def signals(audio, frames, ftimes, duration):
    nb = max(1, int(np.ceil(duration / HOP)))
    t = (np.arange(nb) + 0.5) * HOP

    # audio: 0.1 s frames -> overall level and speech-band level, max per bin
    fl = SR // 10
    na = len(audio) // fl
    loud = np.full(nb, -120.0)
    voice = np.full(nb, -120.0)
    if na:
        a = audio[: na * fl].reshape(na, fl)
        lvl = _db(np.sqrt(np.mean(a ** 2, axis=1)))
        spec = np.abs(np.fft.rfft(a * np.hanning(fl), axis=1)) ** 2
        freqs = np.fft.rfftfreq(fl, 1 / SR)
        band = (freqs >= 300) & (freqs <= 3000)
        vlvl = 10 * np.log10(np.maximum(spec[:, band].sum(axis=1) / spec.sum(axis=1).clip(1e-12), 1e-6)) + lvl
        idx = np.minimum((np.arange(na) * 0.1 / HOP).astype(int), nb - 1)
        np.maximum.at(loud, idx, lvl)
        np.maximum.at(voice, idx, vlvl)
        loud[loud == -120] = np.median(lvl)
        voice[voice == -120] = np.median(vlvl)

    # motion: mean abs difference between consecutive keyframes (on small grey images)
    motion = np.zeros(nb)
    if len(frames) > 1:
        g = frames[:, ::4, ::4].mean(axis=3)
        d = np.abs(np.diff(g, axis=0)).mean(axis=(1, 2))
        motion = np.interp(t, ftimes[1:], d)

    lz, vz, mz = _rz(loud, 1.5), _rz(voice, 1.5), _rz(motion, 1.0)
    act = _smooth(0.45 * lz + 0.25 * vz + 0.30 * mz, 4)
    return t, act


def prior(t, duration, kind):
    if kind != "replay":
        return np.ones_like(t)
    # flat 0.3 until 45 s before the save, rising to 1.0 at 10 s before it
    return 0.3 + 0.7 * np.clip((t - (duration - 45)) / 35, 0, 1)


def moment(t, act, duration, kind):
    """Peak and a suggested keep range. The range grows with the action around the peak,
    so a long fight keeps a long range; if the action fills most of the clip, suggest keeping it whole."""
    if len(t) == 0 or duration < 1:
        return dict(peak=0, strength=0, start=0, end=duration, whole=1)
    score = act * prior(t, duration, kind)
    i = int(np.argmax(score))
    strength = float(act[i])
    thr = max(0.4 * strength, 0.8)
    gap = int(4 / HOP)  # allow up to 4 s of calm inside one moment
    lo = hi = i
    j, calm = i, 0
    while j > 0 and calm <= gap:
        j -= 1
        calm = 0 if act[j] >= thr else calm + 1
        if calm == 0:
            lo = j
    j, calm = i, 0
    while j < len(t) - 1 and calm <= gap:
        j += 1
        calm = 0 if act[j] >= thr else calm + 1
        if calm == 0:
            hi = j
    start = max(0.0, t[lo] - HOP / 2 - 10)   # lead-in so the moment has context
    end = min(duration, t[hi] + HOP / 2 + 3)
    short = 15 - (end - start)                 # never suggest a uselessly short cut
    if short > 0:
        end = min(duration, end + short / 2)
        start = max(0.0, end - 15)
    whole = int(duration <= 25 or (end - start) >= 0.7 * duration)
    return dict(peak=float(t[i]), strength=strength, start=round(start, 1), end=round(end, 1), whole=whole)


def write_thumbs(cid, frames, ftimes, peak):
    if not len(frames):
        return
    pi = int(np.argmin(np.abs(ftimes - peak)))
    Image.fromarray(frames[pi]).save(DATA / "thumbs" / f"{cid}_poster.jpg", quality=82)
    pick = np.linspace(0, len(frames) - 1, 40).astype(int)   # always 40 tiles (repeats on short clips)
    strip = np.concatenate([frames[k, ::2, ::2] for k in pick], axis=1)   # 160x90 tiles in one row
    Image.fromarray(strip).save(DATA / "thumbs" / f"{cid}_sprite.jpg", quality=70)


def analyze(cid):
    c = db.clip(cid)
    path, dur = c["path"], c["duration"] or 0
    audio = _audio(path)
    frames, ftimes = _frames(path, dur)
    t, act = signals(audio, frames, ftimes, dur)
    m = moment(t, act, dur, c["kind"])
    write_thumbs(cid, frames, ftimes, m["peak"])
    top = float(act.max()) if len(act) else 1.0
    # the busiest moment ignoring the end-of-clip weighting; shown as a second option when it is elsewhere
    alt = float(t[int(np.argmax(act))]) if len(act) else 0.0
    (DATA / "analysis" / f"{cid}.json").write_text(json.dumps({
        "hop": HOP, "activity": [round(float(v / max(top, 1e-6)), 3) for v in act],
        "alt": alt if abs(alt - m["peak"]) > 15 else None}))
    # "nothing happens" only makes sense for untouched clips; a trimmed clip is all moment
    quiet = int(dur < 5 or (not c["trimmed"] and dur > 30 and m["strength"] < QUIET))
    db.update(cid, analyzed=1, peak=m["peak"], strength=m["strength"], sug_start=m["start"],
              sug_end=m["end"], sug_whole=m["whole"], quiet=quiet)
    return m
