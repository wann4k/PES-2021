"""Helper ffmpeg: decode preview, encode sumber gambar/video ke format adboard."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from .usm import FFMPEG_DEMUXER, ES_EXT, UsmError

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp", ".gif", ".dds", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv", ".m4v", ".mpg",
              ".mpeg", ".m2v", ".ivf", ".264", ".usm", ".gif"}

_CREATE_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class FfmpegError(RuntimeError):
    pass


def ffmpeg_exe() -> str:
    candidates = []
    exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.append(Path(base) / exe_name)
    candidates.append(Path(sys.argv[0]).resolve().parent / exe_name)
    for c in candidates:
        if c.is_file():
            return str(c)
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise FfmpegError("ffmpeg tidak ditemukan. Letakkan ffmpeg.exe di samping aplikasi.")


def _run(args: list[str], input_bytes: bytes | None = None) -> bytes:
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args],
                          input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=_CREATE_FLAGS)
    if proc.returncode != 0:
        msg = proc.stderr.decode(errors="replace").strip().splitlines()
        raise FfmpegError("\n".join(msg[-8:]) or "ffmpeg gagal")
    return proc.stdout


# --------------------------------------------------------------------------- probe


@dataclass
class MediaInfo:
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration: float = 0.0
    is_image: bool = False


def probe(path: str | os.PathLike) -> MediaInfo:
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          creationflags=_CREATE_FLAGS)
    text = proc.stderr.decode(errors="replace")
    info = MediaInfo(is_image=Path(path).suffix.lower() in IMAGE_EXTS - {".gif"})
    m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", text)
    if m:
        info.width, info.height = int(m.group(1)), int(m.group(2))
    m = re.search(r"([\d.]+) fps", text) or re.search(r"([\d.]+) tbr", text)
    if m:
        info.fps = float(m.group(1))
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", text)
    if m:
        h, mi, s = m.groups()
        info.duration = int(h) * 3600 + int(mi) * 60 + float(s)
    if not info.width:
        raise FfmpegError(f"tidak bisa membaca media: {Path(path).name}")
    return info


# --------------------------------------------------------------------------- decode


def decode_es(es: bytes, codec: str, max_w: int = 960, max_h: int = 540,
              max_frames: int = 900, start_frame: int = 0) -> tuple[int, int, list[bytes]]:
    """Decode elementary stream USM jadi list frame RGB24 (diskalakan untuk preview)."""
    fmt = FFMPEG_DEMUXER.get(codec)
    if not fmt:
        raise UsmError("codec video tidak dikenal / file terenkripsi")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / ("in" + ES_EXT[codec])
        src.write_bytes(es)
        return decode_file(str(src), max_w, max_h, max_frames, ["-f", fmt], start_frame)


def decode_file(path: str, max_w: int = 960, max_h: int = 540, max_frames: int = 900,
                in_args: Iterable[str] = (), start_frame: int = 0) -> tuple[int, int, list[bytes]]:
    info = probe(path) if not in_args else None
    if info is None:
        # ukuran asli belum diketahui -> decode 1 frame untuk mengukur
        proc = subprocess.run([ffmpeg_exe(), "-hide_banner", *in_args, "-i", path],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              creationflags=_CREATE_FLAGS)
        m = re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", proc.stderr.decode(errors="replace"))
        if not m:
            raise FfmpegError("gagal membaca stream video")
        w, h = int(m.group(1)), int(m.group(2))
    else:
        w, h = info.width, info.height
    scale = min(1.0, max_w / w, max_h / h)
    ow, oh = max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)
    vf = f"scale={ow}:{oh}:flags=bicubic"
    if start_frame:
        vf = f"select=gte(n\\,{start_frame})," + vf
    raw = _run([*in_args, "-i", path, "-vf", vf, "-frames:v", str(max_frames),
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    size = ow * oh * 3
    frames = [raw[i:i + size] for i in range(0, len(raw) - size + 1, size)]
    return ow, oh, frames


# --------------------------------------------------------------------------- encode


FIT_MODES = {
    "stretch": "Stretch (penuhi, abaikan rasio)",
    "fit": "Fit (letterbox hitam)",
    "fill": "Fill (crop tengah)",
}


def _scale_filter(w: int, h: int, mode: str) -> str:
    if mode == "fit":
        return (f"scale={w}:{h}:force_original_aspect_ratio=decrease:flags=lanczos,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    if mode == "fill":
        return (f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={w}:{h}")
    return f"scale={w}:{h}:flags=lanczos"


def codec_args(codec: str, fps: float, quality: int) -> list[str]:
    """quality 0..100 (100 = terbaik)."""
    gop = str(max(1, round(fps)))
    if codec in ("mpeg1", "mpeg2"):
        q = max(2, min(31, round(31 - quality * 0.29)))
        enc = "mpeg1video" if codec == "mpeg1" else "mpeg2video"
        return ["-c:v", enc, "-q:v", str(q), "-g", gop, "-bf", "0", "-pix_fmt", "yuv420p",
                "-f", enc]
    if codec == "h264":
        crf = max(10, min(40, round(40 - quality * 0.3)))
        return ["-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-profile:v", "high",
                "-pix_fmt", "yuv420p", "-bf", "0", "-g", gop,
                "-x264-params", "aud=1:repeat-headers=1", "-f", "h264"]
    if codec == "vp9":
        crf = max(10, min(50, round(50 - quality * 0.4)))
        return ["-c:v", "libvpx-vp9", "-crf", str(crf), "-b:v", "0", "-g", gop,
                "-auto-alt-ref", "0", "-lag-in-frames", "0", "-row-mt", "1",
                "-pix_fmt", "yuv420p", "-f", "ivf"]
    raise UsmError(f"codec tidak didukung untuk encode: {codec}")


def encode_source(src: str, codec: str, width: int, height: int, fps: float,
                  frames: int, fit: str = "stretch", quality: int = 80,
                  loop: bool = True, extra_vf: str = "", in_format: str | None = None) -> bytes:
    """Encode gambar/video sumber jadi elementary stream sesuai target adboard."""
    is_image = in_format is None and Path(src).suffix.lower() in IMAGE_EXTS - {".gif"}
    in_args: list[str] = []
    if is_image:
        in_args = ["-loop", "1", "-framerate", f"{fps:.6f}"]
    elif loop:
        in_args = ["-stream_loop", "-1"]
    if in_format:
        in_args += ["-f", in_format]
    vf = f"{_scale_filter(width, height, fit)},fps={fps:.6f},format=yuv420p"
    if extra_vf:
        vf = f"{vf},{extra_vf}"
    return _run([*in_args, "-i", src, "-an", "-vf", vf, "-frames:v", str(frames),
                 "-r", f"{fps:.6f}", *codec_args(codec, fps, quality), "-"])


def encode_rgb_frames(frames: Iterable[bytes], width: int, height: int, fps: float,
                      codec: str, quality: int = 80) -> bytes:
    """Encode frame RGB24 mentah (mis. dari LED text generator)."""
    data = b"".join(frames)
    return _run(["-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
                 "-framerate", f"{fps:.6f}", "-i", "-", "-vf", "format=yuv420p",
                 *codec_args(codec, fps, quality), "-"], input_bytes=data)


def es_to_mp4(es: bytes, codec: str, fps: float, out_path: str) -> None:
    fmt = FFMPEG_DEMUXER.get(codec)
    if not fmt:
        raise UsmError("codec video tidak dikenal / file terenkripsi")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / ("in" + ES_EXT[codec])
        src.write_bytes(es)
        _run(["-f", fmt, "-r", f"{fps:.6f}", "-i", str(src), "-c:v", "libx264", "-crf", "16",
              "-pix_fmt", "yuv420p", "-movflags", "+faststart", out_path])


def es_to_frames(es: bytes, codec: str, out_dir: str, progress: Callable | None = None) -> None:
    fmt = FFMPEG_DEMUXER.get(codec)
    if not fmt:
        raise UsmError("codec video tidak dikenal / file terenkripsi")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / ("in" + ES_EXT[codec])
        src.write_bytes(es)
        _run(["-f", fmt, "-i", str(src), str(Path(out_dir) / "frame_%05d.png")])
