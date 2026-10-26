# Changelog

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
