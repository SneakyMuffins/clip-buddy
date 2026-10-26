# Changelog

## 0.1.2

- **Accurate time-left estimate.** It's now based on gigabytes left and the speed measured so far, instead of a per-clip guess. On a 45-clip test library it was within 6 seconds on average of the real finish time. Before, it could be hours too high on big libraries.
- **Faster reading of big folders:** the folder is walked once per scan instead of once per batch of files.

## 0.1.1

- **Progress you can see:** the top bar shows "Reading folder · 120 files left", then "Analyzing 12 of 340 · about 35 min left".
- **No more waiting for the whole folder:** files are indexed in small batches with analysis in between, so the first suggestions appear within seconds even on a big library.
- **The clip on screen goes first:** a clip you open (and the next two in the queue) jumps the analysis line, so its suggested moment is ready in seconds.
- Clips that arrive while the folder is still being read join the review queue automatically.
- A file ffprobe can't read (corrupt, or still being written) is skipped instead of stalling the folder scan.
- Clearer timeline message while a clip waits for analysis.

## Unreleased

- Windows download: a portable `ClipBuddy.exe` with ffmpeg included, no Python needed. Built and attached to each GitHub release automatically.
- Uses ffmpeg from an `ffmpeg` folder next to the app first, then the one on `PATH`.

## 0.1.0

First release.

- Moment detection from loudness, voice and motion, with an end-of-clip prior for Instant Replay files and a second-opinion marker.
- Keyboard review: keep, keep for 30 days, delete, skip, undo, with an 8 s grace period before files change.
- Lossless trim to the keep range; HEVC compression on NVENC, AMD AMF, Intel Quick Sync or x265, with a VMAF quality gate.
- Detects back-to-back saves and joins them losslessly.
- Library grouped by session with hover-scrub, a Space overview, and a restorable Trash.
- Folder picker on first start; Windows launcher that installs Python and ffmpeg if missing.
