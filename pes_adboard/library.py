"""Logika aplikasi tanpa GUI: scan folder, thumbnail, preview, replace & backup."""
from __future__ import annotations

import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from . import media, textures
from .usm import ES_EXT, FFMPEG_DEMUXER, UsmFile, build_usm, human_size

BACKUP_DIR = "_adboard_backup"
KIND_USM, KIND_TEXTURE, KIND_FTEX = "usm", "texture", "ftex"


def kind_of(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext == ".usm":
        return KIND_USM
    if ext in (".ftex", ".ftexs"):
        return KIND_FTEX
    if ext in textures.TEXTURE_EXTS:
        return KIND_TEXTURE
    return None


@dataclass
class Item:
    path: Path
    kind: str
    size: int
    mtime: float
    info: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.name


def scan_folder(root: str | Path, recursive: bool = True) -> list[Item]:
    root = Path(root)
    items: list[Item] = []
    walker = os.walk(root) if recursive else [(str(root), [], os.listdir(root))]
    for dirpath, dirnames, filenames in walker:
        dirnames[:] = [d for d in dirnames if d != BACKUP_DIR]
        for fn in filenames:
            p = Path(dirpath) / fn
            k = kind_of(p)
            if k is None:
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            items.append(Item(p, k, st.st_size, st.st_mtime))
    items.sort(key=lambda i: (i.kind != KIND_USM, str(i.path).lower()))
    return items


# --------------------------------------------------------------------------- info


def describe(item: Item) -> dict[str, str]:
    if item.kind == KIND_USM:
        return UsmFile.load(item.path).summary()
    if item.kind == KIND_TEXTURE:
        t = textures.texture_info(item.path)
        return {"Format": t.format, "Resolusi": f"{t.width} x {t.height}",
                "Mipmap": str(t.mipmaps), "Ukuran": human_size(item.size)}
    return {"Format": "FTEX (Fox Engine)", "Ukuran": human_size(item.size),
            "Catatan": "Konversi dulu ke DDS (mis. pakai FtexTool) untuk diedit."}


# --------------------------------------------------------------------------- preview


def thumbnail(item: Item, size: tuple[int, int] = (256, 144)) -> Image.Image | None:
    if item.kind == KIND_TEXTURE:
        img = textures.load_image(item.path)
    elif item.kind == KIND_USM:
        usm = UsmFile.load(item.path)
        payloads = usm.video_payloads()
        if not payloads or usm.encrypted:
            return None
        w, h, frames = media.decode_es(b"".join(payloads[:8]), usm.info.codec,
                                       size[0] * 2, size[1] * 2, max_frames=1)
        if not frames:
            return None
        img = Image.frombytes("RGB", (w, h), frames[0])
    else:
        return None
    img.thumbnail(size, Image.LANCZOS)
    return img.convert("RGBA")


@dataclass
class Preview:
    width: int
    height: int
    fps: float
    frames: list[bytes]          # RGB24 untuk video, RGBA untuk gambar
    is_video: bool
    source_size: tuple[int, int]


def load_preview(item: Item, max_w: int = 1600, max_h: int = 900,
                 budget: int = 350 * 1024 * 1024) -> Preview:
    if item.kind == KIND_TEXTURE:
        img = textures.load_image(item.path)
        return Preview(img.width, img.height, 0, [img.tobytes()], False, img.size)
    if item.kind != KIND_USM:
        raise ValueError("format ini belum bisa di-preview")
    usm = UsmFile.load(item.path)
    if usm.encrypted:
        raise ValueError("USM ini terenkripsi / codec tidak dikenal, tidak bisa di-preview")
    i = usm.info
    scale = min(1.0, max_w / max(1, i.width), max_h / max(1, i.height))
    per_frame = max(1, int(i.width * scale) * int(i.height * scale) * 3)
    max_frames = max(1, min(i.total_frames or 10_000, budget // per_frame))
    w, h, frames = media.decode_es(usm.video_es(), i.codec, max_w, max_h, max_frames)
    return Preview(w, h, i.fps or 30.0, frames, True, (i.width, i.height))


# --------------------------------------------------------------------------- backup


def backup_root(target: Path, root: Path | None) -> Path:
    base = root if root and root in target.parents else target.parent
    return base / BACKUP_DIR


def make_backup(target: Path, root: Path | None = None) -> Path:
    broot = backup_root(target, root)
    rel = target.relative_to(broot.parent)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = broot / rel.parent / f"{rel.name}.{stamp}.bak"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, dst)
    return dst


def list_backups(target: Path, root: Path | None = None) -> list[Path]:
    broot = backup_root(target, root)
    rel = target.relative_to(broot.parent)
    folder = broot / rel.parent
    if not folder.is_dir():
        return []
    return sorted(folder.glob(f"{rel.name}.*.bak"), reverse=True)


def restore_backup(target: Path, backup: Path) -> None:
    shutil.copy2(backup, target)


# --------------------------------------------------------------------------- replace


@dataclass
class ReplaceOptions:
    fit: str = "fill"              # stretch | fit | fill
    quality: int = 85              # 0..100
    duration: str = "original"     # original | source | custom
    seconds: float = 10.0
    codec: str = "auto"            # auto | mpeg1 | mpeg2 | h264 | vp9
    dds_format: str = "auto"
    mipmaps: str = "auto"          # auto | yes | no
    backup: bool = True
    loop: bool = True


class _Source:
    """Menyiapkan file sumber agar bisa dibaca ffmpeg (USM/DDS dikonversi dulu)."""

    def __init__(self, path: Path):
        self.path = path
        self.tmp = tempfile.TemporaryDirectory()
        self.ffmpeg_path = str(path)
        self.in_format: str | None = None
        self.is_image = False
        self.fps = 0.0
        self.duration = 0.0
        ext = path.suffix.lower()
        if ext == ".usm":
            usm = UsmFile.load(path)
            if usm.encrypted:
                raise ValueError("USM sumber terenkripsi")
            es = Path(self.tmp.name) / ("src" + ES_EXT[usm.info.codec])
            es.write_bytes(usm.video_es())
            self.ffmpeg_path, self.in_format = str(es), FFMPEG_DEMUXER[usm.info.codec]
            self.fps, self.duration = usm.info.fps, usm.info.duration
        elif ext in textures.TEXTURE_EXTS:
            png = Path(self.tmp.name) / "src.png"
            textures.load_image(path).save(png)
            self.ffmpeg_path, self.is_image = str(png), True
        else:
            info = media.probe(path)
            self.fps, self.duration = info.fps, info.duration
            self.is_image = info.is_image

    def first_frame(self) -> Image.Image:
        if self.is_image:
            return textures.load_image(self.ffmpeg_path)
        args = ["-f", self.in_format] if self.in_format else []
        w, h, frames = media.decode_file(self.ffmpeg_path, 8192, 8192, 1, args)
        return Image.frombytes("RGB", (w, h), frames[0]).convert("RGBA")

    def close(self) -> None:
        self.tmp.cleanup()


def replace_usm(target: Path, source: Path, opts: ReplaceOptions,
                root: Path | None = None) -> str:
    tmpl = UsmFile.load(target)
    info = tmpl.info
    codec = opts.codec if opts.codec != "auto" else info.codec
    if codec not in FFMPEG_DEMUXER:
        codec = "h264"
    fps = info.fps or 30.0
    src = _Source(source)
    try:
        if opts.duration == "custom":
            frames = max(1, round(opts.seconds * fps))
        elif opts.duration == "source" and src.duration > 0 and not src.is_image:
            frames = max(1, round(src.duration * fps))
        else:
            frames = info.total_frames or round(10 * fps)
        es = media.encode_source(src.ffmpeg_path, codec, info.width, info.height, fps, frames,
                                 opts.fit, opts.quality, opts.loop and not src.is_image,
                                 in_format=src.in_format)
    finally:
        src.close()
    data = build_usm(es, codec, info.width, info.height, info.fps_n, info.fps_d, tmpl,
                     tmpl.filename())
    if opts.backup:
        make_backup(target, root)
    target.write_bytes(data)
    return f"{target.name}: {frames} frame, {media_codec_name(codec)}, {human_size(len(data))}"


def replace_texture(target: Path, source: Path, opts: ReplaceOptions,
                    root: Path | None = None) -> str:
    tinfo = textures.texture_info(target)
    src = _Source(source)
    try:
        img = src.first_frame()
    finally:
        src.close()
    img = textures.fit_image(img, tinfo.width, tinfo.height, opts.fit)
    fmt = tinfo.save_format if opts.dds_format == "auto" else opts.dds_format
    mips = tinfo.mipmaps > 1 if opts.mipmaps == "auto" else opts.mipmaps == "yes"
    if opts.backup:
        make_backup(target, root)
    textures.save_texture(img, target, fmt, mips)
    return f"{target.name}: {tinfo.width}x{tinfo.height} {fmt}" + (" + mipmap" if mips else "")


def replace_item(item: Item, source: Path, opts: ReplaceOptions,
                 root: Path | None = None) -> str:
    if item.kind == KIND_USM:
        return replace_usm(item.path, source, opts, root)
    if item.kind == KIND_TEXTURE:
        return replace_texture(item.path, source, opts, root)
    raise ValueError("FTEX belum didukung — konversi dulu ke DDS")


def media_codec_name(codec: str) -> str:
    from .usm import CODEC_NAMES
    return CODEC_NAMES.get(codec, codec)


# --------------------------------------------------------------------------- export


def export_item(item: Item, out_path: Path) -> None:
    ext = out_path.suffix.lower()
    if item.kind == KIND_USM:
        usm = UsmFile.load(item.path)
        if ext == ".mp4":
            media.es_to_mp4(usm.video_es(), usm.info.codec, usm.info.fps or 30.0, str(out_path))
        elif ext in (".png", ".jpg"):
            w, h, frames = media.decode_es(usm.video_es(), usm.info.codec, 8192, 8192, 1)
            Image.frombytes("RGB", (w, h), frames[0]).save(out_path)
        elif out_path.is_dir() or ext == "":
            out_path.mkdir(parents=True, exist_ok=True)
            media.es_to_frames(usm.video_es(), usm.info.codec, str(out_path))
        else:  # elementary stream mentah
            out_path.write_bytes(usm.video_es())
    elif item.kind == KIND_TEXTURE:
        img = textures.load_image(item.path)
        textures.save_texture(img, out_path)
    else:
        raise ValueError("FTEX belum didukung")


def create_usm_from_frames(frames: list[bytes], width: int, height: int, fps: float,
                           out_path: Path, codec: str = "h264", quality: int = 85,
                           template: UsmFile | None = None) -> None:
    es = media.encode_rgb_frames(frames, width, height, fps, codec, quality)
    fps_n, fps_d = (template.info.fps_n, template.info.fps_d) if template else \
        (round(fps * 1000), 1000)
    out_path.write_bytes(build_usm(es, codec, width, height, fps_n, fps_d, template,
                                   out_path.name))
