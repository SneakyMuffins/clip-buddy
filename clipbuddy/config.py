"""Settings. Defaults below; any key can be set in config.json (or the file named by CLIPBUDDY_CONFIG).
The clips folder is chosen in the app on first start and saved there."""
import json
import os
import sys
from pathlib import Path

# The Windows download (ClipBuddy.exe, built with PyInstaller): the folder holding the exe.
FROZEN = getattr(sys, "frozen", False)
ROOT = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent
# Run from a downloaded folder: keep everything next to the app (portable).
# Installed with pip: use the user's app-data folder, never site-packages.
if FROZEN or (ROOT / "ClipBuddy.bat").exists() or (ROOT / "pyproject.toml").exists():
    HOME = ROOT
else:
    HOME = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "ClipBuddy"
    HOME.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = Path(os.environ.get("CLIPBUDDY_CONFIG", HOME / "config.json"))

DEFAULTS = {
    "inbox": None,              # the clips folder; None until chosen in the app
    "keepers": None,            # None = "<clips folder's parent>\Clip Buddy Library\Keepers" (same drive: moves are instant)
    "trash": None,              # None = "<clips folder's parent>\Clip Buddy Library\Trash"
    "data": str(HOME / "data"),
    "port": 8765,
    "compress_keepers": True,   # keepers are re-encoded to HEVC after they are kept
    "cq": 28,                   # HEVC quality; measured VMAF 96-97 at ~4.5x smaller (24: ~98.5 at ~3x)
    "vmaf_min": 95,             # below this the compressed file is thrown away, original kept
    "temp_days": 30,
    "grace_seconds": 8,         # time to press Undo before a decision touches the file
    "session_gap_hours": 2,
}

CFG = dict(DEFAULTS)


def _resolve():
    """Fill in keepers/trash next to the clips folder and make sure the folders exist."""
    if CFG["inbox"]:
        home = Path(CFG["inbox"]).parent / "Clip Buddy Library"   # not "Clip Buddy": the app folder may sit there
        CFG["keepers"] = CFG.get("_keepers_set") or str(home / "Keepers")
        CFG["trash"] = CFG.get("_trash_set") or str(home / "Trash")
        for k in ("keepers", "trash"):
            Path(CFG[k]).mkdir(parents=True, exist_ok=True)


def configured():
    return bool(CFG["inbox"])


def load():
    if CONFIG_FILE.exists():
        CFG.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    CFG["_keepers_set"], CFG["_trash_set"] = CFG["keepers"], CFG["trash"]
    Path(CFG["data"]).mkdir(parents=True, exist_ok=True)
    for sub in ("thumbs", "analysis"):
        (Path(CFG["data"]) / sub).mkdir(exist_ok=True)
    _resolve()


def save(**changes):
    """Write the given keys to config.json (keeping any others already there) and apply them now."""
    saved = json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}
    saved.update(changes)
    CONFIG_FILE.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    CFG.update(changes)
    _resolve()


load()
DATA = Path(CFG["data"])
