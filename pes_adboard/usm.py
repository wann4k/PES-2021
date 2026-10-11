"""Parser, demuxer dan muxer untuk CRI USM (Sofdec2) — format video adboard PES.

Struktur chunk USM (big endian)::

    0x00  4s   signature  ('CRID', '@SFV', '@SFA', '@ALP', ...)
    0x04  u32  ukuran chunk (setelah offset 0x08)
    0x08  u8   unknown (0)
    0x09  u8   offset payload (relatif ke 0x08, biasanya 0x18)
    0x0A  u16  ukuran padding
    0x0C  u8   nomor channel
    0x0D  u8   unknown
    0x0E  u8   unknown
    0x0F  u8   tipe payload (0=stream, 1=header, 2=section end, 3=seek/metadata)
    0x10  u32  frame time
    0x14  u32  frame rate (x100)
    0x18  8s   reserved
    0x20  payload ...

Payload stream video yang digabung berurutan menghasilkan elementary stream
(MPEG-1/2 ``.m2v``, H.264 ``.264`` atau VP9 ``.ivf``) yang bisa dibaca ffmpeg.
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from pathlib import Path

from .utf_table import (FLAG_ROW, T_S8, T_S16, T_S32, T_S64, T_STR, T_U16, T_U32, Column,
                        UtfError, UtfTable)

SIG_CRID = b"CRID"
SIG_VIDEO = b"@SFV"
SIG_AUDIO = b"@SFA"
SIG_ALPHA = b"@ALP"
STMID_VIDEO = struct.unpack(">I", SIG_VIDEO)[0]

PT_STREAM, PT_HEADER, PT_SECTION_END, PT_METADATA = 0, 1, 2, 3

HEADER_END = b"#HEADER END     ===============\x00"
METADATA_END = b"#METADATA END   ===============\x00"
CONTENTS_END = b"#CONTENTS END   ===============\x00"

# nilai kolom VIDEO_HDRINFO.mpeg_codec
CODEC_IDS = {"mpeg1": 1, "mpeg2": 2, "h264": 5, "vp9": 9}
CODEC_NAMES = {
    "mpeg1": "MPEG-1 (Sofdec)", "mpeg2": "MPEG-2 (Sofdec)",
    "h264": "H.264 / AVC", "vp9": "VP9", "unknown": "Tidak dikenal",
}
# format input ffmpeg untuk tiap elementary stream
FFMPEG_DEMUXER = {"mpeg1": "mpegvideo", "mpeg2": "mpegvideo", "h264": "h264", "vp9": "ivf"}
ES_EXT = {"mpeg1": ".m1v", "mpeg2": ".m2v", "h264": ".264", "vp9": ".ivf"}


class UsmError(ValueError):
    pass


@dataclass
class Chunk:
    sig: bytes
    ptype: int
    payload: bytes
    chno: int = 0
    frame_time: int = 0
    frame_rate: int = 0
    unk08: int = 0
    unk0d: int = 0
    unk0e: int = 0
    reserved: bytes = b"\x00" * 8
    offset: int = 0  # posisi di file asal (info saja)

    def to_bytes(self, pad: int | None = None) -> bytes:
        """``pad`` = jumlah byte padding; default mengikuti pola file USM asli."""
        payload_offset = 0x18
        if pad is None:
            pad = default_padding(self.sig, self.ptype, len(self.payload))
        size = payload_offset + len(self.payload) + pad
        head = struct.pack(">4sIBBHBBBBII8s", self.sig, size, self.unk08, payload_offset,
                           pad, self.chno, self.unk0d, self.unk0e, self.ptype & 0x03,
                           self.frame_time, self.frame_rate, self.reserved)
        return head + self.payload + b"\x00" * pad


def default_padding(sig: bytes, ptype: int, payload_len: int, position: int = 0) -> int:
    """Padding seperti yang dipakai encoder CRI asli."""
    if sig == SIG_CRID:  # chunk CRID mengisi sampai batas sektor 0x800
        return (-(position + 0x20 + payload_len)) % 0x800
    if ptype == PT_STREAM:
        return (-payload_len) % 0x20
    if ptype == PT_HEADER:
        return 0x18
    if ptype == PT_METADATA:
        size = 0x20 + payload_len
        return 0xF0 - size if size <= 0xF0 else (-size) % 0x08
    return 0


def read_chunks(data: bytes) -> list[Chunk]:
    chunks: list[Chunk] = []
    pos = 0
    n = len(data)
    while pos + 0x20 <= n:
        sig = data[pos:pos + 4]
        (size,) = struct.unpack_from(">I", data, pos + 4)
        if size < 0x18 or pos + 8 + size > n:
            if sig == b"\x00\x00\x00\x00":
                break
            raise UsmError(f"chunk rusak di offset {pos:#x}")
        (unk08, poff, pad, chno, u0d, u0e, ptype, ftime, frate, reserved) = struct.unpack_from(
            ">BBHBBBBII8s", data, pos + 8)
        start = pos + 8 + poff
        end = pos + 8 + size - pad
        chunks.append(Chunk(sig, ptype & 0x03, bytes(data[start:end]), chno, ftime, frate,
                            unk08, u0d, u0e, reserved, pos))
        pos += 8 + size
    return chunks


# --------------------------------------------------------------------------- codec


def sniff_codec(es: bytes) -> str:
    head = es[:64]
    if head.startswith(b"DKIF"):
        fourcc = head[8:12]
        return "vp9" if fourcc in (b"VP90", b"vp09") else "unknown"
    if head.startswith(b"\x00\x00\x01\xb3"):
        return "mpeg2" if es.find(b"\x00\x00\x01\xb5", 0, 4096) != -1 else "mpeg1"
    i = head.find(b"\x00\x00\x01")
    if i != -1 and i + 3 < len(head):
        nal = head[i + 3] & 0x1F
        if nal in (1, 5, 6, 7, 8, 9) and not head[i + 3] & 0x80:
            return "h264"
    return "unknown"


@dataclass
class Frame:
    data: bytes
    key: bool


def _find_all(buf: bytes, pat: bytes) -> list[int]:
    out, i = [], buf.find(pat)
    while i != -1:
        out.append(i)
        i = buf.find(pat, i + 1)
    return out


def split_frames(es: bytes, codec: str) -> list[Frame]:
    """Pecah elementary stream menjadi satu payload per frame."""
    if codec == "vp9":
        return _split_ivf(es)
    if codec in ("mpeg1", "mpeg2"):
        return _split_mpeg(es)
    if codec == "h264":
        return _split_h264(es)
    raise UsmError(f"codec tidak didukung: {codec}")


def _split_ivf(es: bytes) -> list[Frame]:
    if not es.startswith(b"DKIF"):
        raise UsmError("stream VP9 harus berformat IVF")
    (hdr_len,) = struct.unpack_from("<H", es, 6)
    frames: list[Frame] = []
    pos = hdr_len
    while pos + 12 <= len(es):
        (size,) = struct.unpack_from("<I", es, pos)
        body = es[pos + 12:pos + 12 + size]
        key = bool(body) and _vp9_is_key(body[0])
        chunk = es[pos:pos + 12 + size]
        if not frames:
            chunk = es[:hdr_len] + chunk  # header IVF ikut di frame pertama
        frames.append(Frame(chunk, key))
        pos += 12 + size
    return frames


def _vp9_is_key(b0: int) -> bool:
    profile = ((b0 >> 5) & 1) | (((b0 >> 4) & 1) << 1)
    shift = 1 if profile == 3 else 0
    show_existing = (b0 >> (3 - shift)) & 1
    frame_type = (b0 >> (2 - shift)) & 1
    return not show_existing and frame_type == 0


def _split_mpeg(es: bytes) -> list[Frame]:
    pics = _find_all(es, b"\x00\x00\x01\x00")
    if not pics:
        raise UsmError("tidak ada picture MPEG di stream")
    frames: list[Frame] = []
    start = 0
    for idx, p in enumerate(pics):
        # header (sequence/GOP) sebelum picture berikutnya ikut ke frame berikutnya
        if idx + 1 < len(pics):
            nxt = pics[idx + 1]
            cut = nxt
            for code in (b"\x00\x00\x01\xb3", b"\x00\x00\x01\xb8"):
                j = es.find(code, p + 4, nxt)
                if j != -1:
                    cut = min(cut, j)
        else:
            cut = len(es)
        ptype = (es[p + 5] >> 3) & 0x07 if p + 5 < len(es) else 0
        frames.append(Frame(es[start:cut], ptype == 1))
        start = cut
    return frames


def _nal_units(es: bytes) -> list[tuple[int, int]]:
    """Kembalikan (posisi start code, tipe NAL) untuk tiap NAL."""
    out = []
    i = es.find(b"\x00\x00\x01")
    while i != -1:
        s = i - 1 if i > 0 and es[i - 1] == 0 else i
        if i + 3 < len(es):
            out.append((s, es[i + 3] & 0x1F))
        i = es.find(b"\x00\x00\x01", i + 3)
    return out


def _split_h264(es: bytes) -> list[Frame]:
    nals = _nal_units(es)
    if not nals:
        raise UsmError("tidak ada NAL H.264 di stream")
    # batas access unit: AUD(9) atau SPS/PPS/SEI/slice pertama setelah slice
    bounds: list[int] = []
    seen_slice = False
    for pos, t in nals:
        if t == 9 or (t in (6, 7, 8) and seen_slice):
            bounds.append(pos)
            seen_slice = False
        elif t in (1, 5):
            if seen_slice and not _first_mb_zero(es, pos):
                continue
            if seen_slice:
                bounds.append(pos)
            seen_slice = True
    if not bounds or bounds[0] != 0:
        bounds.insert(0, 0)
    bounds.append(len(es))
    frames = []
    for a, b in zip(bounds, bounds[1:]):
        if b <= a:
            continue
        unit = es[a:b]
        key = any(t == 5 for _, t in _nal_units(unit))
        frames.append(Frame(unit, key))
    # gabungkan unit tanpa slice (mis. hanya SPS/PPS) ke frame berikutnya
    merged: list[Frame] = []
    carry = b""
    for f in frames:
        if not any(t in (1, 5) for _, t in _nal_units(f.data)):
            carry += f.data
            continue
        merged.append(Frame(carry + f.data, f.key))
        carry = b""
    if carry and merged:
        merged[-1] = Frame(merged[-1].data + carry, merged[-1].key)
    return merged


def _first_mb_zero(es: bytes, pos: int) -> bool:
    i = es.find(b"\x00\x00\x01", pos) + 4
    return i < len(es) and bool(es[i] & 0x80)  # ue(v) first_mb_in_slice == 0


# --------------------------------------------------------------------------- file


@dataclass
class VideoInfo:
    width: int = 0
    height: int = 0
    fps_n: int = 30000
    fps_d: int = 1000
    total_frames: int = 0
    codec: str = "unknown"
    alpha: bool = False

    @property
    def fps(self) -> float:
        return self.fps_n / self.fps_d if self.fps_d else 0.0

    @property
    def duration(self) -> float:
        return self.total_frames / self.fps if self.fps else 0.0


@dataclass
class UsmFile:
    path: Path | None
    chunks: list[Chunk]
    crid: UtfTable | None = None
    video_header: UtfTable | None = None
    seek_info: UtfTable | None = None
    info: VideoInfo = field(default_factory=VideoInfo)
    encrypted: bool = False
    file_size: int = 0

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, path: str | os.PathLike) -> "UsmFile":
        data = Path(path).read_bytes()
        usm = cls.from_bytes(data)
        usm.path = Path(path)
        return usm

    @classmethod
    def from_bytes(cls, data: bytes) -> "UsmFile":
        if not data.startswith(SIG_CRID):
            raise UsmError("bukan file USM (signature CRID tidak ada)")
        chunks = read_chunks(data)
        usm = cls(None, chunks, file_size=len(data))
        for c in chunks:
            try:
                if c.sig == SIG_CRID and c.ptype == PT_HEADER and usm.crid is None:
                    usm.crid = UtfTable.parse(c.payload)
                elif c.sig == SIG_VIDEO and c.chno == 0 and c.ptype == PT_HEADER \
                        and usm.video_header is None and c.payload.startswith(b"@UTF"):
                    usm.video_header = UtfTable.parse(c.payload)
                elif c.sig == SIG_VIDEO and c.chno == 0 and c.ptype == PT_METADATA \
                        and usm.seek_info is None and c.payload.startswith(b"@UTF"):
                    usm.seek_info = UtfTable.parse(c.payload)
            except UtfError:
                pass
        usm._fill_info()
        return usm

    def _fill_info(self) -> None:
        h = self.video_header
        info = self.info
        if h is not None:
            info.width = int(h.get("disp_width") or h.get("width") or 0)
            info.height = int(h.get("disp_height") or h.get("height") or 0)
            info.fps_n = int(h.get("framerate_n") or 30000)
            info.fps_d = int(h.get("framerate_d") or 1000)
            info.total_frames = int(h.get("total_frames") or 0)
            info.alpha = bool(h.get("alpha_type"))
        frames = self.video_payloads()
        if not info.total_frames:
            info.total_frames = len(frames)
        sniffed = sniff_codec(frames[0]) if frames else "unknown"
        info.codec = sniffed
        if sniffed == "unknown" and frames:
            self.encrypted = True
            if h is not None:
                cid = int(h.get("mpeg_codec") or 0)
                info.codec = {v: k for k, v in CODEC_IDS.items()}.get(cid, "unknown")

    # ------------------------------------------------------------- streams
    def streams(self) -> list[tuple[bytes, int]]:
        seen = []
        for c in self.chunks:
            if c.sig != SIG_CRID and (c.sig, c.chno) not in seen:
                seen.append((c.sig, c.chno))
        return seen

    def video_payloads(self, chno: int = 0, sig: bytes = SIG_VIDEO) -> list[bytes]:
        return [c.payload for c in self.chunks
                if c.sig == sig and c.chno == chno and c.ptype == PT_STREAM]

    def video_es(self, chno: int = 0) -> bytes:
        return b"".join(self.video_payloads(chno))

    def stream_es(self, sig: bytes, chno: int = 0) -> bytes:
        return b"".join(self.video_payloads(chno, sig))

    def filename(self) -> str:
        if self.crid is not None:
            name = self.crid.get("filename", 0)
            if name:
                return str(name)
        return self.path.name if self.path else "movie.usm"

    def summary(self) -> dict[str, str]:
        i = self.info
        streams = ", ".join(f"{s.decode(errors='replace')}#{n}" for s, n in self.streams())
        return {
            "Codec": CODEC_NAMES.get(i.codec, i.codec) + (" (terenkripsi?)" if self.encrypted else ""),
            "Resolusi": f"{i.width} x {i.height}",
            "FPS": f"{i.fps:.3f}".rstrip("0").rstrip("."),
            "Total frame": str(i.total_frames),
            "Durasi": f"{i.duration:.2f} detik",
            "Alpha": "Ya" if i.alpha else "Tidak",
            "Stream": streams or "-",
            "Nama internal": self.filename(),
            "Ukuran": human_size(self.file_size),
        }


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


# --------------------------------------------------------------------------- mux


def _default_crid() -> UtfTable:
    cols = [
        Column("fmtver", T_S32, FLAG_ROW),
        Column("filename", T_STR, FLAG_ROW),
        Column("filesize", T_S32, FLAG_ROW),
        Column("datasize", T_S32, FLAG_ROW),
        Column("stmid", T_S32, FLAG_ROW),
        Column("chno", T_S16, FLAG_ROW),
        Column("minchk", T_S16, FLAG_ROW),
        Column("minbuf", T_S32, FLAG_ROW),
        Column("avbps", T_S32, FLAG_ROW),
    ]
    rows = [
        dict(fmtver=16777984, filename="movie.usm", filesize=0, datasize=0, stmid=0,
             chno=-1, minchk=1, minbuf=0, avbps=0),
        dict(fmtver=16777984, filename="movie.m2v", filesize=0, datasize=0,
             stmid=STMID_VIDEO, chno=0, minchk=3, minbuf=0, avbps=0),
    ]
    return UtfTable("CRIUSF_DIR_STREAM", cols, rows)


def _default_video_header() -> UtfTable:
    names_s32 = ["width", "height", "mat_width", "mat_height", "disp_width", "disp_height",
                 "scrn_width"]
    names_s32b = ["alpha_type", "total_frames", "framerate_n", "framerate_d", "metadata_count",
                  "metadata_size", "ixsize", "pre_padding", "max_picture_size", "color_space",
                  "picture_type"]
    cols = [Column(n, T_S32, FLAG_ROW) for n in names_s32]
    cols += [Column("mpeg_dcprec", T_S8, FLAG_ROW), Column("mpeg_codec", T_S8, FLAG_ROW)]
    cols += [Column(n, T_S32, FLAG_ROW) for n in names_s32b]
    row = {c.name: 0 for c in cols}
    row.update(mpeg_dcprec=11, framerate_n=30000, framerate_d=1000, metadata_count=1)
    return UtfTable("VIDEO_HDRINFO", cols, [row])


def _default_seek_info() -> UtfTable:
    cols = [
        Column("ofs_byte", T_S64, FLAG_ROW),
        Column("ofs_frmid", T_U32, FLAG_ROW),
        Column("num_skip", T_U16, FLAG_ROW),
        Column("resv", T_U16, FLAG_ROW),
    ]
    return UtfTable("VIDEO_SEEKINFO", cols, [])


def _copy_table(t: UtfTable) -> UtfTable:
    return UtfTable(t.name, [Column(c.name, c.type, c.storage, c.default) for c in t.columns],
                    [dict(r) for r in t.rows], t.version)


def build_usm(es: bytes, codec: str, width: int, height: int, fps_n: int = 30000,
              fps_d: int = 1000, template: UsmFile | None = None,
              filename: str = "movie.usm") -> bytes:
    """Bangun file USM (video saja) dari elementary stream.

    Bila ``template`` diberikan (USM asli dari game), tabel header, nilai unknown dan
    pola timing chunk-nya dipakai ulang supaya hasilnya sekompatibel mungkin.
    """
    frames = split_frames(es, codec)
    if not frames:
        raise UsmError("stream video kosong")

    # ---- tabel dari template / default
    if template is not None and template.crid is not None:
        crid = _copy_table(template.crid)
        keep = [r for r in crid.rows
                if int(r.get("stmid") or 0) == 0
                or (int(r.get("stmid") or 0) == STMID_VIDEO and int(r.get("chno") or 0) == 0)]
        crid.rows = keep if len(keep) >= 2 else _default_crid().rows
    else:
        crid = _default_crid()
        crid.rows[0]["filename"] = filename
    vh = _copy_table(template.video_header) if template is not None and \
        template.video_header is not None else _default_video_header()
    seek = _copy_table(template.seek_info) if template is not None and \
        template.seek_info is not None else _default_seek_info()

    tmpl_chunks = template.chunks if template is not None else []

    def tmpl_chunk(sig: bytes, ptype: int) -> Chunk | None:
        for c in tmpl_chunks:
            if c.sig == sig and c.ptype == ptype and c.chno == 0:
                return c
        return None

    def mk(sig: bytes, ptype: int, payload: bytes, **kw) -> Chunk:
        t = tmpl_chunk(sig, ptype)
        base = dict(unk08=t.unk08, unk0d=t.unk0d, unk0e=t.unk0e, reserved=t.reserved,
                    frame_rate=t.frame_rate, frame_time=t.frame_time) if t else \
            dict(frame_rate=30)
        base.update(kw)
        return Chunk(sig, ptype, payload, **base)

    # timing frame (frame_time / frame_rate = detik)
    tmpl_data = [c for c in tmpl_chunks if c.sig == SIG_VIDEO and c.chno == 0
                 and c.ptype == PT_STREAM]
    if len(tmpl_data) >= 2 and tmpl_data[1].frame_time > tmpl_data[0].frame_time:
        rate_field = tmpl_data[0].frame_rate
        step = tmpl_data[1].frame_time - tmpl_data[0].frame_time
        t0 = tmpl_data[0].frame_time
    else:
        rate_field = round(fps_n * 100 / fps_d)
        step, t0 = 100, 0

    data_chunks = [mk(SIG_VIDEO, PT_STREAM, f.data, frame_time=t0 + i * step,
                      frame_rate=rate_field) for i, f in enumerate(frames)]
    data_bytes = [c.to_bytes() for c in data_chunks]
    max_pic = max(len(f.data) for f in frames)
    duration = len(frames) * fps_d / fps_n if fps_n else 1.0
    avbps = int(len(es) * 8 / max(duration, 1e-6))

    # ---- update header video
    def setv(t: UtfTable, k: str, v, row: int = 0) -> None:
        if t.column(k) is not None:
            t.set(k, v, row)

    # mat_* = ukuran "matrix" ter-align; ikuti pola template bila ada
    tmpl_vh = template.video_header if template is not None else None
    aligned = bool(tmpl_vh) and tmpl_vh.get("mat_width") != tmpl_vh.get("disp_width")
    mat_align = 16 if aligned else 1
    max_packed = max(len(b) for b in data_bytes) - 8
    for k, v in (("width", width), ("height", height), ("disp_width", width),
                 ("disp_height", height),
                 ("mat_width", -(-width // mat_align) * mat_align),
                 ("mat_height", -(-height // mat_align) * mat_align),
                 ("total_frames", len(frames)), ("framerate_n", fps_n),
                 ("framerate_d", fps_d), ("ixsize", max_packed),
                 ("mpeg_codec", CODEC_IDS.get(codec, 0)), ("alpha_type", 0)):
        setv(vh, k, v)
    if tmpl_vh is None:
        setv(vh, "mpeg_dcprec", 0 if codec == "vp9" else 11)
    elif tmpl_vh.get("max_picture_size"):
        setv(vh, "max_picture_size", max_pic)

    keys = [i for i, f in enumerate(frames) if f.key] or [0]
    # metadata_size: file asli kadang berisi jumlah keyframe, kadang ukuran chunk
    meta_is_count = tmpl_vh is None or (
        template.seek_info is not None
        and tmpl_vh.get("metadata_size") == len(template.seek_info.rows))
    minbuf = round(max_packed * 1.98746)
    minbuf += (-minbuf) % 0x10

    end_chunk = mk(SIG_VIDEO, PT_SECTION_END, CONTENTS_END)
    out = b""
    for _ in range(4):  # ulangi sampai ukuran stabil (offset saling bergantung)
        seek.rows = [dict(ofs_byte=0, ofs_frmid=i, num_skip=0, resv=0) for i in keys]
        seek_chunk = mk(SIG_VIDEO, PT_METADATA, seek.to_bytes()).to_bytes()
        setv(vh, "metadata_size", len(keys) if meta_is_count else len(seek_chunk))
        setv(vh, "metadata_count", 1)
        hdr = mk(SIG_VIDEO, PT_HEADER, vh.to_bytes()).to_bytes()
        hdr_end = mk(SIG_VIDEO, PT_SECTION_END, HEADER_END).to_bytes()
        meta_end = mk(SIG_VIDEO, PT_SECTION_END, METADATA_END).to_bytes()
        crid_bytes = mk(SIG_CRID, PT_HEADER, crid.to_bytes()).to_bytes()
        head_len = len(crid_bytes) + len(hdr) + len(hdr_end) + len(seek_chunk) + len(meta_end)
        offsets, pos = [], head_len
        for b in data_bytes:
            offsets.append(pos)
            pos += len(b)
        total = pos + len(end_chunk.to_bytes())
        seek.rows = [dict(ofs_byte=offsets[i], ofs_frmid=i, num_skip=0, resv=0) for i in keys]
        seek_chunk = mk(SIG_VIDEO, PT_METADATA, seek.to_bytes()).to_bytes()

        crid.set("filesize", total, 0)
        crid.set("avbps", avbps, 0)
        crid.set("minbuf", minbuf, 0)
        crid.set("filesize", len(es), 1)
        crid.set("avbps", avbps, 1)
        crid.set("minbuf", minbuf, 1)
        if crid.column("datasize") is not None:
            crid.set("datasize", 0, 1)
        if template is None:
            crid.set("filename", Path(filename).with_suffix(ES_EXT.get(codec, ".bin")).name, 1)
        crid_bytes = mk(SIG_CRID, PT_HEADER, crid.to_bytes()).to_bytes()

        out = b"".join([crid_bytes, hdr, hdr_end, seek_chunk, meta_end, *data_bytes,
                        end_chunk.to_bytes()])
        if len(out) == total:
            break
    return out
