"""Tes inti: tabel @UTF, mux/demux USM, DDS, replace end-to-end."""
from __future__ import annotations

import struct
from pathlib import Path

import pytest
from PIL import Image

from pes_adboard import library, media, textures
from pes_adboard.usm import CODEC_IDS, UsmFile, build_usm, split_frames
from pes_adboard.utf_table import FLAG_DEFAULT, FLAG_ROW, T_S16, T_S32, T_STR, Column, UtfTable

try:
    media.ffmpeg_exe()
    HAS_FFMPEG = True
except media.FfmpegError:
    HAS_FFMPEG = False

needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg tidak tersedia")
W, H, FPS = 256, 32, 30.0


def test_utf_roundtrip():
    t = UtfTable("TEST", [Column("a", T_S32, FLAG_ROW), Column("b", T_STR, FLAG_ROW),
                          Column("c", T_S16, FLAG_DEFAULT, 7)],
                 [{"a": -5, "b": "halo", "c": 7}, {"a": 9, "b": "dunia", "c": 7}])
    data = t.to_bytes()
    assert data[:4] == b"@UTF" and len(data) % 8 == 0
    t2 = UtfTable.parse(data)
    assert t2.name == "TEST" and t2.rows == t.rows
    t2.set("c", 3, 1)  # kolom konstan -> per-row
    t3 = UtfTable.parse(t2.to_bytes())
    assert [r["c"] for r in t3.rows] == [7, 3]


@pytest.fixture(scope="module")
def src_video(tmp_path_factory) -> Path:
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg tidak tersedia")
    out = tmp_path_factory.mktemp("src") / "src.mp4"
    media._run(["-f", "lavfi", "-i", "testsrc=size=320x60:rate=30", "-t", "2",
                "-pix_fmt", "yuv420p", str(out)])
    return out


@needs_ffmpeg
@pytest.mark.parametrize("codec", ["mpeg1", "mpeg2", "h264", "vp9"])
def test_usm_mux_demux(src_video, codec):
    es = media.encode_source(str(src_video), codec, W, H, FPS, 45)
    frames = split_frames(es, codec)
    assert len(frames) == 45 and frames[0].key
    data = build_usm(es, codec, W, H, template=None, filename="ad.usm")
    usm = UsmFile.from_bytes(data)
    assert usm.video_es() == es
    assert usm.info.codec == codec and not usm.encrypted
    assert (usm.info.width, usm.info.height, usm.info.total_frames) == (W, H, 45)
    assert usm.crid.get("filesize", 0) == len(data)
    assert usm.crid.get("chno", 0) == -1
    assert usm.video_header.get("mpeg_codec") == CODEC_IDS[codec]
    # seek info menunjuk ke chunk keyframe yang benar
    for row in usm.seek_info.rows:
        off = row["ofs_byte"]
        assert data[off:off + 4] == b"@SFV" and data[off + 15] & 3 == 0
    w, h, decoded = media.decode_es(usm.video_es(), codec)
    assert (w, h, len(decoded)) == (W, H, 45)
    # build ulang dengan template = deterministik
    assert build_usm(es, codec, W, H, template=usm) == data


@needs_ffmpeg
def test_replace_end_to_end(tmp_path, src_video):
    es = media.encode_source(str(src_video), "h264", W, H, FPS, 60)
    (tmp_path / "sub").mkdir()
    target = tmp_path / "sub" / "led.usm"
    target.write_bytes(build_usm(es, "h264", W, H, filename="led.usm"))
    textures.save_texture(Image.new("RGBA", (512, 64), "red"), tmp_path / "board.dds", "DXT1")
    logo = tmp_path / "logo.png"
    Image.new("RGB", (200, 50), "blue").save(logo)

    items = {i.name: i for i in library.scan_folder(tmp_path)}
    assert {"led.usm", "board.dds", "logo.png"} <= set(items)

    library.replace_item(items["led.usm"], logo, library.ReplaceOptions(), tmp_path)
    usm = UsmFile.load(target)
    assert (usm.info.width, usm.info.height, usm.info.total_frames) == (W, H, 60)
    assert usm.info.codec == "h264"
    assert library.list_backups(target, tmp_path)

    library.replace_item(items["board.dds"], src_video,
                         library.ReplaceOptions(mipmaps="yes"), tmp_path)
    info = textures.texture_info(tmp_path / "board.dds")
    assert (info.width, info.height, info.format) == (512, 64, "DXT1") and info.mipmaps > 1

    pv = library.load_preview(items["led.usm"])
    assert pv.is_video and len(pv.frames) == 60


def test_dds_mipmaps(tmp_path):
    img = Image.linear_gradient("L").convert("RGBA").resize((128, 32))
    for fmt in textures.DDS_SAVE_FORMATS:
        p = tmp_path / f"t_{fmt}.dds"
        textures.save_texture(img, p, fmt, True)
        info = textures.texture_info(p)
        assert (info.width, info.height, info.mipmaps) == (128, 32, 8)
        assert textures.load_image(p).size == (128, 32)
        assert struct.unpack_from("<I", p.read_bytes(), 28)[0] == 8
