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

## Reading any recorder's files

Clip Buddy doesn't depend on one recorder's naming. For every `.mp4`, `.mkv` or `.mov` it works out:

- **When:** the first date and time anywhere in the name, with any separators: NVIDIA's `2026.01.18 - 20.48.30`, AMD's `2025.03.29-01.08`, OBS and Game Bar's `2024-10-27 21-33-11`, or `20241027_213311`. The file's modified time, which every recorder writes when it finishes saving, is used when it agrees with the name, because it is exact to the second (joins need that). Without a date in the name the modified time alone is used.
- **Replay or recording:** a replay if the name says so (`replay`, `DVR`, `instant`, `clip`, `highlight`). Replays get the end-of-clip weighting above.
- **Game:** the text before the date, without recorder words (`Delta Force_replay_2025.03.29-01.08_clip_1` → `Delta Force`). Without one, the folder name, unless that folder holds clips of many games (`NVIDIA`, `Radeon ReLive`, `Captures`, `Videos` and so on); then "Other".

Nothing that moves or deletes a file depends on reading its name.

**The suggested range is adaptive.** It grows outward from the peak while activity stays above 40% of the peak, allowing 4 s calm gaps, then adds 10 s of lead-in and 3 s after (minimum 15 s). If that covers 70% or more of the clip, or the clip is 25 s or shorter, the suggestion is to keep the whole clip.

**A second opinion.** On a 100-clip test library, the end-weighted peak and the raw busiest moment disagreed on about half the clips. When they are more than 15 s apart, the raw one is shown as a hollow marker and `A` moves the range there. Neither guess is right every time, so the app shows both and measures which one you use: every keep records the suggested and the chosen range, and the Space tab shows how often they matched.

**Junk flags:** "Tiny" for clips under 5 s (a double-pressed hotkey), and "Nothing much happens" when the peak activity of an untouched clip over 30 s stays below a threshold calibrated to flag about 5% of real clips.

## Saving space without visible loss

| Step | What it does | Quality |
|---|---|---|
| **Trim** | Cuts to the keep range with a stream copy (no re-encode). The start snaps to the previous keyframe: at most 0.5 s earlier on NVIDIA files, several seconds on some other recorders' files and on HEVC. | identical |
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
- VMAF on a 10 s sample from the middle must be at least `vmaf_min` (95), with frames matched by index rather than timestamp, because trimmed MP4s carry edit lists that make timestamp matching drift by a frame. The sample uses the real average frame rate, since variable-frame-rate captures run well below their nominal rate,
- it must be at least 10% smaller.

If any check fails, the original is kept and the error is shown. Clips that are already HEVC or AV1 are not re-encoded, and say so.

### Encoder choice

On first use Clip Buddy runs a tiny test encode with each encoder and uses the first one that works: **NVENC → AMD AMF → Intel Quick Sync → x265 (CPU)**. Listing an encoder in ffmpeg doesn't mean the hardware supports it, which is why it tests a real encode. The quality settings are matched to NVENC's: x265 needs CRF 20 to equal NVENC cq 28 (CRF 24 only reached VMAF 92.9). AMF and Quick Sync start one step on the safe side, and the VMAF check rejects anything below the bar. Force a specific encoder with `"encoder"` in `config.json`.

## Back-to-back saves

If you press the hotkey again shortly after a save, NVIDIA's second clip does **not** overlap the first: the replay buffer is cleared on every save, so the second clip starts where the first ended, and its length equals the time since the previous save. Clip Buddy detects these runs (same game and format, each clip starting within 3 s of the previous one's end) and can **join** them into one file losslessly (concat, no re-encode).

Before the parts move to the Trash, the joined file is checked: every part must have the same tracks (codec, resolution, audio layout), every track of the result must have the full length (so no audio dropped out), and the result must decode without errors around each point where two clips meet. Clips that were already trimmed or compressed can't be joined, because they no longer continue each other exactly.

## Safety

The rule: **Clip Buddy never destroys a clip you didn't delete yourself**, whatever fails along the way (a crash, closing the app mid-job, a full disk, a file open in a player, a drive that is away).

- **Nothing is deleted until you empty the Trash.** Deleted clips, and originals replaced by trimmed or compressed versions, move to Clip Buddy's own `Trash` folder, and every one can be restored. Clip Buddy doesn't use the Windows Recycle Bin, because files bigger than its size limit are deleted permanently instead of recycled, and 1 GB clips can hit that.
- **Every decision waits 8 s** before it touches the file, so `U` really undoes it.
- **New files are checked before old ones move.** Trim, compress and join write a new file, verify it, and only then move the original to the Trash.
- **Empty trash deletes only what you deleted**, plus originals whose trimmed or compressed version is in place. It keeps the original of a kept clip whose new version is missing, and any file it has no record of (for example one put there by another copy of the app, which may be a kept clip). Those can be restored, or deleted with a separate confirmation.
- **Every step can run again.** If the app closes in the middle of a job, the job runs again at the next start and first looks where the file really is, so a move is never made twice and a clip is never trimmed twice. A trimmed or compressed copy that was finished but not yet in place is put in place, never deleted.
- **One move at a time, all or nothing.** A file is moved with a single rename. If another program has it open, the move waits and retries, and if it still can't, nothing changes. Across drives the copy is removed again when the original can't be deleted, so there is never a second, unrecorded copy.
- **One job per clip.** A rename never runs while the same clip is being compressed, and an Undo that says "Undone" really stopped the job.
- **Records survive a missing drive.** A folder that is briefly away (a drive asleep or unplugged) keeps all its records.
- **A history of every file change** (move, replace, delete) is kept in `data\history.jsonl`, so where a clip went always has an answer.
- **Only one copy runs.** A second start opens the running copy, and warns if it is a different version, instead of starting its own background work on the same library.
- **Kept clips live in `Clip Buddy Library`** next to your clips folder: on the same drive, so moves are instant, and never inside the app's own folder, so deleting the app can't delete your clips.

## Architecture

Python serves a single-page UI on `http://localhost:8765`. ffmpeg and ffprobe do all the video work, numpy the analysis, Pillow the thumbnails, and SQLite stores the state.

Background work runs in parallel lanes:
- **Quick jobs** (deletes, moves, renames, restores) have their own lane, so they never wait behind an encode.
- **Slow jobs** (trim + compress, join) run one at a time: the VMAF check is already CPU-heavy, and GPUs limit concurrent encodes.
- **Reading and analysis** run on one thread per 4 CPU threads, at most 4. On an 8-core/16-thread CPU, 4 at once analyzed 3.9× faster than 1; 6 or 8 were no faster, because disk reads become the limit.

A clip is claimed by one lane at a time, so it is never analyzed while a job moves or trims it. Analysis order: the clip on screen and the next two first, then unreviewed clips oldest first, then kept clips that only need fresh thumbnails after a trim.

| File | Job |
|---|---|
| `clipbuddy/config.py` | settings, saved in `config.json` |
| `clipbuddy/scanner.py` | find clips, read any recorder's file names, ffprobe |
| `clipbuddy/analyzer.py` | moment score, suggested range, thumbnails |
| `clipbuddy/encoders.py` | pick the HEVC encoder this PC can run |
| `clipbuddy/actions.py` | trash/restore, keep, trim, compress, join, rename |
| `clipbuddy/worker.py` | the background lanes: quick jobs, slow jobs, analysis |
| `clipbuddy/server.py` | HTTP API and static UI |
| `clipbuddy/static/` | the UI (plain HTML/CSS/JS, no build step) |
