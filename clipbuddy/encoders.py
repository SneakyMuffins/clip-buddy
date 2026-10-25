"""Picks the HEVC encoder this PC can actually run: NVIDIA, then AMD, then Intel GPU, else the CPU.

Each one gets a quality setting meant to look the same (VMAF ~96-97). `cq` in config.json is the NVENC
scale; the others are offsets from it. Measured on a 10 s 1440p60 racing-game clip (original 106 MB):
  NVENC cq 28   -> 23.6 MB, VMAF 96.3
  x265 crf 20   -> 24.4 MB, VMAF 96.7   (crf 24 was 92.9: x265's scale is much harsher)
AMF and Quick Sync settings are not measured yet; they start a step on the safe side, and the
VMAF check in actions.compress() throws away anything below vmaf_min anyway.
"""
from . import scanner
from .config import CFG

# name -> (label shown in the app, ffmpeg video args for a given cq)
ENCODERS = {
    "nvenc": ("NVIDIA GPU (NVENC)", lambda cq: [
        "-c:v", "hevc_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", str(cq), "-b:v", "0",
        "-maxrate", "150M", "-bufsize", "300M", "-spatial-aq", "1", "-temporal-aq", "1"]),
    "amf": ("AMD GPU (AMF)", lambda cq: [
        "-c:v", "hevc_amf", "-usage", "transcoding", "-quality", "quality", "-rc", "cqp",
        "-qp_i", str(cq - 4), "-qp_p", str(cq - 4)]),
    "qsv": ("Intel GPU (Quick Sync)", lambda cq: [
        "-c:v", "hevc_qsv", "-preset", "veryslow", "-global_quality", str(cq - 4)]),
    "x265": ("CPU (x265, slower)", lambda cq: [
        "-c:v", "libx265", "-preset", "medium", "-crf", str(cq - 8), "-pix_fmt", "yuv420p",
        "-x265-params", "log-level=error"]),
}
ORDER = ["nvenc", "amf", "qsv", "x265"]
_found = None


def _works(name):
    """A tiny real encode: listing an encoder in ffmpeg doesn't mean this PC's hardware supports it."""
    r = scanner.run(["ffmpeg", "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=0.5",
                     *ENCODERS[name][1](CFG["cq"]), "-f", "null", "-"])
    return r.returncode == 0


def pick():
    """The encoder to use: config "encoder" if set (nvenc/amf/qsv/x265), else the first that works."""
    global _found
    forced = CFG.get("encoder")
    if forced and forced != "auto":
        if forced not in ENCODERS:
            raise ValueError(f'unknown encoder "{forced}" in config.json; use one of {", ".join(ORDER)} or "auto"')
        return forced
    if _found is None:
        _found = next((n for n in ORDER if _works(n)), None)
    return _found


def args():
    name = pick()
    if not name:
        raise RuntimeError("no HEVC encoder works on this PC (ffmpeg is missing libx265)")
    return ENCODERS[name][1](CFG["cq"])


def label():
    try:
        name = pick()
    except ValueError as e:
        return str(e)
    return ENCODERS[name][0] if name else "none available"
