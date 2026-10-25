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
