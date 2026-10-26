# Contributing

Bug reports, ideas and pull requests are welcome.

## Reporting a bug

Open an issue with:
- what you did and what happened,
- your GPU and Windows version,
- the text in the Clip Buddy console window, if there is an error,
- for a moment-detection miss: the game, and where the real moment was versus the suggested range.

## Making a change

1. Fork, then create a branch.
2. Set up: `python -m venv .venv` and `.venv\Scripts\pip install -e .[dev]`.
3. Make the change. Keep the UI dependency-free (plain HTML/CSS/JS, no build step).
4. Run `ruff check .` and `python -m pytest`. If you touched compression, also run it with `CLIPBUDDY_TEST_ENCODER=x265`.
5. Open a pull request that says what changed and why.

## Especially useful

- **AMD and Intel results.** If you have one of these GPUs, the Space tab shows which encoder was picked. Tell us the size and quality score (in the Trash note) of a few compressed clips, so the AMF and Quick Sync settings can be tuned like NVENC's.
- **Moment-detection misses** in specific games, with the timestamps.
- **Other recorders' file names** (AMD ReLive, OBS, Steam) for better date and game parsing.

## Releasing

1. Update `version` in `pyproject.toml` and add a section to `CHANGELOG.md`.
2. Tag and push: `git tag v0.2.0` then `git push origin v0.2.0`. A tag with a suffix (`v0.2.0-rc1`) makes a pre-release.
3. The `release` workflow builds `ClipBuddy-<version>-windows.zip`, starts it from a fresh folder to check it answers, and attaches it to the GitHub release. The tag must match the version in `pyproject.toml`.

To build the same zip on your PC: download a BtbN `ffmpeg-n8.1-latest-win64-gpl-shared-8.1.zip` from [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds/releases/tag/latest), unzip it, then run `pip install -e . pyinstaller==6.22.3` and `python tools/build_windows.py --ffmpeg <unzipped folder>`. Running the workflow by hand (**Actions → release → Run workflow**) builds it without releasing, or, with *draft release* ticked, makes a draft release you can check and then publish.

Why the download is built this way:
- **PyInstaller onedir**, not onefile: onefile unpacks itself to a temp folder on every start, which is slower and trips antivirus more often.
- **Bundled ffmpeg** sits in `ffmpeg\` next to `ClipBuddy.exe`. The app uses it first and falls back to ffmpeg on `PATH` (see `TOOLS` in `clipbuddy/scanner.py`).
- **BtbN 8.1, GPL, shared.** GPL for libx265 and libvmaf; shared because ffmpeg and ffprobe then share their DLLs (about 190 MB unpacked instead of 330 MB). 8.1 rather than 9.0: BtbN builds 9.0 and later against NVENC SDK 13.1, which needs NVIDIA driver 610+, and on older drivers Clip Buddy would fall back to CPU encoding. 8.1 needs driver 570+.
- **Portable data.** The exe keeps `config.json` and `data\` next to itself, like the `ClipBuddy.bat` folder does.
