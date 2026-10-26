"""Builds the Windows download: dist/ClipBuddy-<version>-windows.zip, a portable folder with ClipBuddy.exe
(PyInstaller onedir), a bundled ffmpeg + ffprobe, and the licenses. Then unzips it into an empty folder,
starts it and checks it answers, the same way a user would run it.

    pip install -e . pyinstaller
    python tools/build_windows.py --ffmpeg <unzipped BtbN ffmpeg-n*-win64-gpl-shared build> [--version 0.1.0]

Used by .github/workflows/release.yml; runs the same on a PC.
"""
import argparse
import importlib.metadata as md
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build" / "windows"
DIST = ROOT / "dist"
# Build tools that end up in the venv but not in the exe.
NOT_BUNDLED = {"pyinstaller", "pyinstaller-hooks-contrib", "altgraph", "pefile", "pywin32-ctypes", "packaging",
               "pip", "setuptools", "wheel", "clip-buddy", "pytest", "pluggy", "iniconfig", "ruff", "colorama"}

README = """Clip Buddy {version} for Windows
================================

Start: double-click ClipBuddy.exe. Clip Buddy opens in your browser and asks where your clips are.
Keep the console window open while you use it; close it to stop Clip Buddy.

Windows may show "Windows protected your PC" the first time (the exe is not code-signed):
click "More info", then "Run anyway".

Your settings and Clip Buddy's database are stored in this folder (config.json and data\\),
so keep the folder somewhere permanent, for example Documents\\Clip Buddy.
To update: close Clip Buddy and unzip the new version over this folder.

Help, source code and issues: https://github.com/SneakyMuffins/clip-buddy
Licenses: LICENSE.txt (Clip Buddy, MIT), ffmpeg\\ (ffmpeg, GPL), THIRD-PARTY-LICENSES.txt (Python and libraries).
"""

FFMPEG_NOTE = """ffmpeg, ffprobe and the DLLs in this folder are an unmodified build of FFmpeg ({ffmpeg_version})
from https://github.com/BtbN/FFmpeg-Builds (win64, GPL, shared variant, with libx265 and libvmaf).

FFmpeg is licensed under the GNU General Public License version 3: see LICENSE.txt in this folder.
Clip Buddy only runs these programs; it is a separate program under the MIT license.

Source code of this build: FFmpeg https://github.com/FFmpeg/FFmpeg (the version above), and the build
scripts and the source of every bundled library at https://github.com/BtbN/FFmpeg-Builds.
"""


def sh(*cmd, **kw):
    print(">", " ".join(map(str, cmd)), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def ffmpeg_version(exe):
    return subprocess.run([exe, "-version"], capture_output=True, text=True, check=True).stdout.split("\n")[0]


def check_ffmpeg(ffmpeg):
    """The bundled build must have what Clip Buddy needs: libx265 (CPU encoder) and libvmaf (quality gate)."""
    enc = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    flt = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    for name, listing in (("libx265", enc), ("hevc_nvenc", enc), ("hevc_amf", enc), ("hevc_qsv", enc), ("libvmaf", flt)):
        if f" {name} " not in listing:
            sys.exit(f"bundled ffmpeg lacks {name}")
    # A real 1 s encode plus VMAF, as the app does it.
    with tempfile.TemporaryDirectory() as t:
        out = Path(t) / "x.mp4"
        sh(ffmpeg, "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=1", "-c:v", "libx265",
           "-x265-params", "log-level=error", out)
        r = subprocess.run([ffmpeg, "-hide_banner", "-i", out, "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=1",
                            "-lavfi", "[0:v][1:v]libvmaf", "-f", "null", "-"], capture_output=True, text=True)
        if "VMAF score" not in r.stderr:
            sys.exit("bundled ffmpeg: libvmaf did not produce a score\n" + r.stderr[-2000:])


def third_party_licenses():
    """Python's license and every bundled library's license files, from the build environment."""
    py_license = Path(sys.base_prefix) / "LICENSE.txt"
    parts = [f"Python {sys.version.split()[0]} (Python Software Foundation License)\n\n"
             + (py_license.read_text(encoding="utf-8", errors="replace") if py_license.exists()
                else "https://docs.python.org/3/license.html")]
    for d in sorted(md.distributions(), key=lambda d: d.metadata["Name"].lower()):
        name = d.metadata["Name"]
        if name.lower() in NOT_BUNDLED:
            continue
        files = [f for f in d.files or [] if "licen" in f.name.lower() or "copying" in f.name.lower()
                 or "notice" in f.name.lower() or "authors" in f.name.lower()]
        texts = [f.read_text() for f in files if f.locate().is_file()]
        lic = d.metadata.get("License-Expression") or d.metadata.get("License") or ""
        parts.append(f"{name} {d.version}  {lic.splitlines()[0] if lic else ''}".rstrip() + "\n\n"
                     + ("\n\n".join(texts) if texts else "(no license file shipped with the package)"))
    return ("\n\n" + "=" * 78 + "\n\n").join(parts)


def build(version, ffmpeg_dir):
    shutil.rmtree(BUILD, ignore_errors=True)
    sh(sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console", "--name", "ClipBuddy",
       "--distpath", BUILD / "dist", "--workpath", BUILD / "work", "--specpath", BUILD,
       "--collect-submodules", "uvicorn", "--collect-submodules", "clipbuddy",
       "--add-data", f"{ROOT / 'clipbuddy' / 'static'}{os.pathsep}clipbuddy/static",
       ROOT / "tools" / "exe_entry.py")
    app = BUILD / "dist" / "ClipBuddy"

    (app / "ffmpeg").mkdir()
    # The "shared" build: ffmpeg and ffprobe share their DLLs, half the size of two static exes.
    for f in [ffmpeg_dir / "bin" / "ffmpeg.exe", ffmpeg_dir / "bin" / "ffprobe.exe", *(ffmpeg_dir / "bin").glob("*.dll")]:
        shutil.copy2(f, app / "ffmpeg" / f.name)
    shutil.copy2(ffmpeg_dir / "LICENSE.txt", app / "ffmpeg" / "LICENSE.txt")
    fv = ffmpeg_version(app / "ffmpeg" / "ffmpeg.exe")
    (app / "ffmpeg" / "README.txt").write_text(FFMPEG_NOTE.format(ffmpeg_version=fv), encoding="utf-8")
    check_ffmpeg(app / "ffmpeg" / "ffmpeg.exe")

    shutil.copy2(ROOT / "LICENSE", app / "LICENSE.txt")
    (app / "README.txt").write_text(README.format(version=version), encoding="utf-8")
    (app / "THIRD-PARTY-LICENSES.txt").write_text(third_party_licenses(), encoding="utf-8")

    DIST.mkdir(exist_ok=True)
    out = DIST / f"ClipBuddy-{version}-windows.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(app.rglob("*")):
            if f.is_file():
                z.write(f, Path("Clip Buddy") / f.relative_to(app))
    print(f"{out}  ({out.stat().st_size / 1e6:.0f} MB)  {fv}")
    return out, fv


def smoke(zip_path, port=8765):
    """Unzip into an empty folder, start the exe like a double-click would (minus the browser), check it answers."""
    with tempfile.TemporaryDirectory() as t:
        zipfile.ZipFile(zip_path).extractall(t)
        app = Path(t) / "Clip Buddy"
        env = {**os.environ, "CLIPBUDDY_NO_BROWSER": "1"}
        env.pop("CLIPBUDDY_CONFIG", None)
        # Started from another folder, as Explorer may: the app must still keep its data next to the exe.
        p = subprocess.Popen([app / "ClipBuddy.exe"], cwd=tempfile.gettempdir(), env=env, stdin=subprocess.DEVNULL)
        try:
            info = None
            for _ in range(60):
                try:
                    info = json.load(urllib.request.urlopen(f"http://localhost:{port}/api/setup", timeout=2))
                    break
                except OSError:
                    if p.poll() is not None:
                        sys.exit(f"ClipBuddy.exe exited with code {p.returncode}")
                    time.sleep(1)
            if info is None:
                sys.exit("ClipBuddy.exe did not answer on /api/setup within 60 s")
            page = urllib.request.urlopen(f"http://localhost:{port}/", timeout=5).read().decode("utf-8", "replace")
            checks = {
                "/api/setup says not configured (fresh folder)": info.get("configured") is False,
                "UI page is served": "<html" in page.lower(),
                "data folder created next to the exe": (app / "data").is_dir(),
            }
            for what, ok in checks.items():
                print(("ok    " if ok else "FAIL  ") + what)
            if not all(checks.values()):
                sys.exit("smoke test failed")
        finally:
            p.kill()
            p.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg", type=Path, required=True, help="unzipped BtbN ffmpeg build (has bin\\ and LICENSE.txt)")
    ap.add_argument("--version", default=md.version("clip-buddy"))
    ap.add_argument("--no-smoke", action="store_true")
    a = ap.parse_args()
    out, fv = build(a.version, a.ffmpeg.resolve())
    if not a.no_smoke:
        smoke(out)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"zip={out.as_posix()}\nffmpeg_version={fv}\n")


if __name__ == "__main__":
    main()
