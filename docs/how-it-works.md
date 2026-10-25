# How Clip Buddy works

## The problem

NVIDIA Instant Replay saves the last N seconds (90 s by default) every time you press the hotkey. At 1440p60 the NVIDIA app records H.264 at roughly 85–90 Mbit/s, so every clip is about **1 GB**, mostly footage around the moment you actually wanted. A library of 100 clips is ~80 GB, and checking each one means scrubbing through 90 seconds of video.

Clip Buddy attacks both sides: it makes the keep/delete decision take a few seconds, and it shrinks what you keep.

## Finding the moment

Instant Replay saves what happened *before* you pressed the hotkey, so the end of the file is the moment you reacted, and what you clipped is almost always in the last 5–40 seconds.

For every 0.5 s of the clip the analyzer measures:

| Signal | How |
|---|---|
| **Loudness** | audio level, compared with this clip's own typical level (robust z-score: median and MAD) |
| **Voice** | energy in the 300–3000 Hz speech band: shouting, laughing |
| **Motion** | mean difference between consecutive keyframes: crashes, kill cams, cuts |

They are combined as `0.45·loud + 0.25·voice + 0.30·motion` and smoothed over 2 s. For Instant Replay files the score is then weighted by a **position prior**: 0.3 until 45 s before the save, rising to 1.0 at 10 s before it. Manual recordings get no prior.

Only keyframes are decoded (every 0.5 s in NVIDIA's files), so analysis takes a few seconds per clip.

**The suggested range is adaptive.** It grows outward from the peak while activity stays above 40% of the peak, allowing 4 s calm gaps, then adds 10 s of lead-in and 3 s after (minimum 15 s). If that covers 70% or more of the clip, or the clip is 25 s or shorter, the suggestion is to keep the whole clip.

**A second opinion.** On a 100-clip test library, the end-weighted peak and the raw busiest moment disagreed on about half the clips. When they are more than 15 s apart, the raw one is shown as a hollow marker and `A` moves the range there. Neither guess is right every time, so the app shows both and measures which one you use: every keep records the suggested and the chosen range, and the Space tab shows how often they matched.

**Junk flags:** "Tiny" for clips under 5 s (a double-pressed hotkey), and "Nothing much happens" when the peak activity of an untouched clip over 30 s stays below a threshold calibrated to flag about 5% of real clips.

## Saving space without visible loss

| Step | What it does | Quality |
|---|---|---|
| **Trim** | Cuts to the keep range with a stream copy (no re-encode). The start snaps to the previous keyframe, at most 0.5 s earlier. | identical |
| **Compress** | Re-encodes kept clips to HEVC | VMAF ~96–97 |

Measured on 1440p60 clips (VMAF: 100 = identical; above ~95 is not noticeable in normal viewing):

| Encoder and setting | Bitrate | VMAF |
|---|---|---|
| Original H.264 (NVIDIA app) | 83–92 Mbit/s | 100 |
| NVENC HEVC, cq 24 | 27–31 Mbit/s | 98.4–98.6 |
| **NVENC HEVC, cq 28 (default)** | 16–19 Mbit/s | 96.3–97.2 |
| x265, CRF 20 (CPU fallback) | ~19 Mbit/s | 96.7 |

Trimming a 90 s clip to its moment and then compressing it typically takes it from ~1 GB to 30–60 MB.

Every compressed file is checked before it replaces the original:
- its duration must match,
- VMAF on a 10 s sample from the middle must be at least `vmaf_min` (95), with frames matched by index rather than timestamp, because trimmed MP4s carry edit lists that make timestamp matching drift by a frame,
- it must be at least 10% smaller.

If any check fails, the original is kept and the error is shown.

### Encoder choice

On first use Clip Buddy runs a tiny test encode with each encoder and uses the first one that works: **NVENC → AMD AMF → Intel Quick Sync → x265 (CPU)**. Listing an encoder in ffmpeg doesn't mean the hardware supports it, which is why it tests a real encode. The quality settings are matched to NVENC's: x265 needs CRF 20 to equal NVENC cq 28 (CRF 24 only reached VMAF 92.9). AMF and Quick Sync start one step on the safe side, and the VMAF check rejects anything below the bar. Force a specific encoder with `"encoder"` in `config.json`.

## Back-to-back saves

If you press the hotkey again shortly after a save, NVIDIA's second clip does **not** overlap the first: the replay buffer is cleared on every save, so the second clip starts where the first ended, and its length equals the time since the previous save. Clip Buddy detects these runs (same game, each clip starting within 3 s of the previous one's end) and can **join** them into one file losslessly (concat, no re-encode).

## Safety

- **Nothing is deleted until you empty the Trash.** Deleted clips, and originals replaced by trimmed or compressed versions, move to Clip Buddy's own `Trash` folder, and every one can be restored. Clip Buddy doesn't use the Windows Recycle Bin, because files bigger than its size limit are deleted permanently instead of recycled, and 1 GB clips can hit that.
- **Every decision waits 8 s** before it touches the file, so `U` really undoes it.
- **New files are checked before old ones move.** Trim, compress and join write a new file, verify it, and only then move the original to the Trash.
- **Kept clips live in `Clip Buddy Library`** next to your clips folder: on the same drive, so moves are instant, and never inside the app's own folder, so deleting the app can't delete your clips.

## Architecture

Python serves a single-page UI on `http://localhost:8765`. ffmpeg and ffprobe do all the video work, numpy the analysis, Pillow the thumbnails, and SQLite stores the state. One background worker runs file jobs, analysis and folder rescans one at a time.

| File | Job |
|---|---|
| `clipbuddy/config.py` | settings, saved in `config.json` |
| `clipbuddy/scanner.py` | find clips, parse NVIDIA file names, ffprobe |
| `clipbuddy/analyzer.py` | moment score, suggested range, thumbnails |
| `clipbuddy/encoders.py` | pick the HEVC encoder this PC can run |
| `clipbuddy/actions.py` | trash/restore, keep, trim, compress, join, rename |
| `clipbuddy/worker.py` | background jobs, analysis, rescans |
| `clipbuddy/server.py` | HTTP API and static UI |
| `clipbuddy/static/` | the UI (plain HTML/CSS/JS, no build step) |
