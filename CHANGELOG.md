# Changelog

## 0.1.5

Fixes bugs that could lose kept clips. **Update now.** Until the update has started once, don't empty the Trash.

**How to update:** close Clip Buddy and unzip the new version over the old folder. If you unzip it somewhere new, copy the `data` folder and `config.json` from the old folder into the new one first: they hold your decisions, and the repair below needs them.

### Kept clips that went missing

- **Fixed: keeping a clip could leave its only copy in the Trash.** It happened to every clip whose name doesn't follow NVIDIA's pattern: all AMD ReLive clips, and clips trimmed in other apps (`... .DVR-00.04.06.082-00.04.59.430.mp4`). The original went to the Trash, a later step failed, the compressed copy was cleaned up at the next start, and emptying the Trash then deleted the clip for good.
- **Repair on first start.** Clips caught by this bug go back to Keepers as kept, with the range you chose cut again, your title, and compressed again. This also works for game folders with an apostrophe (Baldur's Gate 3, Tom Clancy's...). Nothing can be repaired once the Trash has been emptied.
- **Empty trash only deletes what you deleted.** It keeps any Trash file it has no record of (it may be a kept clip, for example after using another copy of the app) and the original of a kept clip whose new version is missing. The Trash screen marks them. Files of unknown origin can be restored, or deleted with their own hold-to-confirm button.

### Nothing destroys a clip when something fails

- Every step can run again after the app was closed in the middle of it: a move is never made twice, a clip is never trimmed twice, and a finished trimmed or compressed copy is put in place instead of deleted.
- **Fixed:** deleting or compressing a clip that was open in another program (a player, Explorer's preview, or Clip Buddy's own player) left a full copy in the Trash and the clip stuck. Moves now wait and retry, and if they still can't, nothing changes and the job tries again a minute later.
- **Fixed:** an Undo could say "Undone" while the delete still ran. Undo now only reports what it really stopped.
- **Fixed:** a rename (title) could run while the same clip was being compressed, and the title was lost. One job per clip at a time.
- **Fixed:** deleting a clip from a bulk action while its keep was pending could put a deleted clip in Keepers.
- **Fixed:** a drive or folder that was briefly away (asleep, unplugged, renamed) wiped every decision, rating and tag of its clips.
- **Fixed:** joining clips saved in different formats, or already-compressed clips, could make a broken file or drop the audio, and the parts went to the Trash anyway. Joins now check the tracks of every part, the full length of every track, and that the result plays cleanly where the clips meet, before any part moves.
- **Fixed:** joins failed for games with an apostrophe in the path, a join cut off half way left a duplicate, and joining a kept clip left the result outside Keepers.
- **Fixed:** startup cleanup deleted any of your own files with `.cb-` in the name. It only touches the exact temp files Clip Buddy writes.
- **Fixed:** starting a second copy (for example the new version while the old one was open) could reset and repeat the running copy's jobs. Only one copy runs now, and a different version says so instead of opening the old one.
- **Fixed:** after changing the clips folder, the old Trash could never be emptied.
- Every file change (move, replace, delete) is written to `data\history.jsonl`.

### Any recorder, not just NVIDIA

- File names from any recorder are read: AMD ReLive, OBS, Xbox Game Bar, Steam, Medal and others. The game, the time and whether it is a replay come from the name, with the file's own time for accuracy. Before, other names lost the game (it became the folder name), the time and the replay weighting, and back-to-back clips weren't found.
- `.mkv` and `.mov` files are included (OBS records `.mkv` by default).
- Setup suggests the default folders of AMD, Xbox Game Bar, Medal, Outplayed and SteelSeries too.
- **Fixed:** keeping a range of a clip with keyframes far apart (OBS, most HEVC) always failed.
- **Fixed:** compressing a variable-frame-rate clip failed with "quality check failed to run".
- Clips that are already HEVC or AV1 say why they weren't compressed.

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
