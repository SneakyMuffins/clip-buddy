# Changelog

## 0.1.5

Fixes a bug that could lose kept clips. **Update now**, and don't empty the Trash until the update has started once.

- **Fixed: keeping a clip with a non-standard file name could leave its only copy in the Trash.** Affected names don't follow NVIDIA's pattern, for example clips cut with the Windows Photos trimmer (`... .DVR-00.04.06.082-00.04.59.430.mp4`). The original went to the Trash, then a later step failed. The smaller compressed copy was then cleaned up at the next start, so emptying the Trash deleted the clip for good.
- **Repair on first start:** clips already caught by this bug are put back. If the compressed copy is still there it takes its place; otherwise the original is moved from the Trash back to Keepers and compressed again. Nothing is repaired once the Trash has been emptied.
- **Kept clips are now protected at every step.** If replacing a clip with its trimmed or compressed version fails at any point, the original is put back where it was. A finished copy left by an interrupted job is put in place at start instead of being deleted. Empty trash never deletes an original whose kept version is missing.
- **Fixed:** a clip moved by a job while the folder was being read could lose its record (its rating, tags and title).
- **Fixed:** a keep that was cut off by closing the app no longer tries to trim the clip a second time when it runs again.

## 0.1.4

- **Work runs in parallel.** Clips are analyzed several at a time (one per 4 CPU threads, at most 4). On an 8-core CPU that's about 4× faster. Analysis no longer stops while your keeps are being compressed.
- **Deletes don't wait behind compression.** Quick jobs (deletes, moves, renames, restores) have their own lane. Before, a delete could wait half an hour behind a queue of compressions. Now it's done in seconds.
- **The top bar shows what is really happening:** "Applying your decisions · 26 left · about 15 min" and "Analyzing 4 of 66 · about 8 min" side by side. Before, it said "Analyzing" while it was actually compressing.
- **"Failed" is gone for clips that just weren't worth compressing.** A clip that would lose visible quality, or that is already compact (older recordings made at a low bitrate), is kept as it is with a note on the clip instead of an error. "Compress all" skips it from then on.
- New clips are analyzed before kept clips that only need fresh thumbnails after a trim.
- New setting `analysis_workers` (`"auto"` by default).

## 0.1.3

Safety and correctness fixes. Updating is recommended.

- **Fixed: emptying the Trash could permanently delete a clip that never reached it.** A clip you pressed D on is only moved after its 8 s undo window. Emptying the Trash inside that window deleted it straight from your clips folder. Empty trash now deletes only files that are really in the Trash folder.
- **Fixed: the Trash folder could be bigger than the app said.** A second copy of Clip Buddy (for example the `.bat` version and the exe, or an update unzipped into a new folder) keeps its own records, so it didn't know what the other copy had put in Trash. Each scan now takes in every file in the Trash and Keepers folders: unknown Trash files show up in the Trash (restorable), and unknown kept clips count as kept instead of unreviewed.
- **Fixed: other websites could control Clip Buddy.** A page open in your browser could send commands to the local app, for example to empty your Trash. Changes now need a header only Clip Buddy's own page sends, and the app answers only on `localhost`.
- **Fixed:** a file open in another program no longer stops Empty trash halfway, and the space freed before it is still counted.
- **Fixed:** choosing a parent folder (for example `Videos` instead of `Videos\NVIDIA`) no longer pulls Clip Buddy's own Keepers and Trash back in as unreviewed clips.
- **Fixed:** restoring a file without a known origin works (it goes back to the clips folder), and restored originals get their thumbnails and timeline.
- **Better compression:** if a clip misses the quality bar, it's retried once at a gentler setting before the original is kept. A racing clip that scored 94.8 now compresses at 98.2.
- Starting Clip Buddy while it's already running opens the running copy instead of failing.
- Temp files left by an interrupted trim, compress or join are cleaned up at start, and clips that failed to analyze get one retry per start.
- The Trash screen shows where the Trash folder is, and doesn't count clips still in their undo window.

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
