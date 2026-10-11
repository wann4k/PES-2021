"""Baca / tulis tekstur adboard (DDS, PNG, TGA, ...)."""
from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

TEXTURE_EXTS = {".dds", ".png", ".tga", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
DDS_SAVE_FORMATS = ["DXT5", "DXT1", "DXT3", "RGBA"]

_DDSD_MIPMAPCOUNT = 0x20000
_DDSCAPS_COMPLEX = 0x8
_DDSCAPS_TEXTURE = 0x1000
_DDSCAPS_MIPMAP = 0x400000

_DXGI_NAMES = {71: "BC1", 74: "BC2", 77: "BC3", 80: "BC4", 83: "BC5", 95: "BC6H",
               98: "BC7", 99: "BC7", 28: "RGBA", 87: "BGRA"}


@dataclass
class TextureInfo:
    width: int
    height: int
    format: str
    mipmaps: int = 1

    @property
    def save_format(self) -> str:
        """Format DDS terdekat yang bisa ditulis ulang."""
        f = self.format.upper()
        if f in ("DXT1", "BC1"):
            return "DXT1"
        if f in ("DXT3", "BC2"):
            return "DXT3"
        if f in ("RGBA", "BGRA", "ARGB", "RGB"):
            return "RGBA"
        return "DXT5"


def dds_info(data: bytes) -> TextureInfo:
    if data[:4] != b"DDS " or len(data) < 128:
        raise ValueError("bukan file DDS")
    height, width = struct.unpack_from("<II", data, 12)
    (mips,) = struct.unpack_from("<I", data, 28)
    pf_flags = struct.unpack_from("<I", data, 80)[0]
    fourcc = data[84:88]
    if pf_flags & 0x4:
        if fourcc == b"DX10":
            (dxgi,) = struct.unpack_from("<I", data, 128)
            fmt = _DXGI_NAMES.get(dxgi, f"DXGI {dxgi}")
        else:
            fmt = fourcc.decode("ascii", errors="replace").strip("\x00") or "?"
    else:
        bits = struct.unpack_from("<I", data, 88)[0]
        fmt = "RGBA" if pf_flags & 0x1 or bits == 32 else "RGB"
    return TextureInfo(width, height, fmt, max(1, mips))


def texture_info(path: str | Path) -> TextureInfo:
    p = Path(path)
    if p.suffix.lower() == ".dds":
        with open(p, "rb") as f:
            return dds_info(f.read(256))
    with Image.open(p) as im:
        return TextureInfo(im.width, im.height, im.mode, 1)


def load_image(path: str | Path) -> Image.Image:
    with Image.open(path) as im:
        im.load()
        return im.convert("RGBA")


def fit_image(img: Image.Image, width: int, height: int, mode: str = "stretch",
              background: tuple[int, int, int, int] = (0, 0, 0, 255)) -> Image.Image:
    img = img.convert("RGBA")
    if mode == "fit":
        inner = ImageOps.contain(img, (width, height), Image.LANCZOS)
        canvas = Image.new("RGBA", (width, height), background)
        canvas.alpha_composite(inner, ((width - inner.width) // 2, (height - inner.height) // 2))
        return canvas
    if mode == "fill":
        return ImageOps.fit(img, (width, height), Image.LANCZOS)
    return img.resize((width, height), Image.LANCZOS)


def _encode_level(img: Image.Image, fmt: str) -> tuple[bytes, bytes]:
    buf = io.BytesIO()
    if fmt == "RGBA":
        img.convert("RGBA").save(buf, "DDS")
    else:
        img.convert("RGBA").save(buf, "DDS", pixel_format=fmt)
    data = buf.getvalue()
    return data[:128], data[128:]


def dds_bytes(img: Image.Image, fmt: str = "DXT5", mipmaps: bool = True) -> bytes:
    if fmt not in DDS_SAVE_FORMATS:
        fmt = "DXT5"
    img = img.convert("RGBA")
    header, body = _encode_level(img, fmt)
    levels = [body]
    if mipmaps:
        w, h = img.size
        while w > 1 or h > 1:
            w, h = max(1, w // 2), max(1, h // 2)
            levels.append(_encode_level(img.resize((w, h), Image.LANCZOS), fmt)[1])
    head = bytearray(header)
    if len(levels) > 1:
        flags = struct.unpack_from("<I", head, 8)[0] | _DDSD_MIPMAPCOUNT
        struct.pack_into("<I", head, 8, flags)
        struct.pack_into("<I", head, 28, len(levels))
        caps = struct.unpack_from("<I", head, 108)[0]
        struct.pack_into("<I", head, 108, caps | _DDSCAPS_TEXTURE | _DDSCAPS_COMPLEX
                         | _DDSCAPS_MIPMAP)
    return bytes(head) + b"".join(levels)


def save_texture(img: Image.Image, path: str | Path, fmt: str = "DXT5",
                 mipmaps: bool = True) -> None:
    p = Path(path)
    if p.suffix.lower() == ".dds":
        p.write_bytes(dds_bytes(img, fmt, mipmaps))
    elif p.suffix.lower() in (".jpg", ".jpeg", ".bmp"):
        img.convert("RGB").save(p)
    else:
        img.save(p)
