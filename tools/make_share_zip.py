"""Builds dist/Clip Buddy.zip for a release: the app only, without the local database, thumbnails,
settings or Python environment.  Run: python tools/make_share_zip.py"""
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = ["ClipBuddy.bat", "README.md", "LICENSE", "requirements.txt", "clipbuddy"]
out = ROOT / "dist" / "Clip Buddy.zip"
out.parent.mkdir(exist_ok=True)

with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for item in INCLUDE:
        p = ROOT / item
        files = [p] if p.is_file() else sorted(f for f in p.rglob("*") if f.is_file() and "__pycache__" not in f.parts)
        for f in files:
            z.write(f, Path("Clip Buddy") / f.relative_to(ROOT))
print(f"{out}  ({out.stat().st_size / 1e3:.0f} KB)")
