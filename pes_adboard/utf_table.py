"""Reader / writer untuk tabel CRI ``@UTF`` (dipakai di header file USM).

Struktur (big endian):

    0x00  '@UTF'
    0x04  u32  ukuran tabel (setelah offset 0x08)
    0x08  u16  versi
    0x0A  u16  offset rows        (relatif ke 0x08)
    0x0C  u32  offset strings     (relatif ke 0x08)
    0x10  u32  offset data        (relatif ke 0x08)
    0x14  u32  offset nama tabel  (di dalam string table)
    0x18  u16  jumlah kolom
    0x1A  u16  lebar row
    0x1C  u32  jumlah row
    0x20  schema kolom ...
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

# flag kolom
FLAG_NAME = 0x10
FLAG_DEFAULT = 0x20  # nilai konstan disimpan di schema
FLAG_ROW = 0x40      # nilai disimpan per row

# tipe kolom (urutan signed-dulu, sesuai file USM asli: chno=-1 bertipe 0x02)
T_S8, T_U8, T_S16, T_U16, T_S32, T_U32, T_S64, T_U64, T_F32, T_F64, T_STR, T_DATA = range(12)

_FMT = {
    T_S8: ">b", T_U8: ">B", T_S16: ">h", T_U16: ">H",
    T_S32: ">i", T_U32: ">I", T_S64: ">q", T_U64: ">Q",
    T_F32: ">f", T_F64: ">d",
}
_SIZE = {T_U8: 1, T_S8: 1, T_U16: 2, T_S16: 2, T_U32: 4, T_S32: 4,
         T_U64: 8, T_S64: 8, T_F32: 4, T_F64: 8, T_STR: 4, T_DATA: 8}


class UtfError(ValueError):
    pass


@dataclass
class Column:
    name: str
    type: int
    storage: int            # FLAG_ROW, FLAG_DEFAULT, atau 0 (kosong / zero)
    default: Any = None     # nilai bila storage == FLAG_DEFAULT


@dataclass
class UtfTable:
    name: str
    columns: list[Column]
    rows: list[dict[str, Any]] = field(default_factory=list)
    version: int = 0  # USM memakai 0 (CPK memakai 1)

    # ------------------------------------------------------------ helpers
    def column(self, name: str) -> Column | None:
        for c in self.columns:
            if c.name == name:
                return c
        return None

    def get(self, name: str, row: int = 0, default: Any = None) -> Any:
        if row >= len(self.rows):
            return default
        return self.rows[row].get(name, default)

    def set(self, name: str, value: Any, row: int = 0) -> None:
        """Ubah nilai. Kolom konstan/zero otomatis dijadikan kolom per-row."""
        col = self.column(name)
        if col is None:
            raise KeyError(name)
        if col.storage != FLAG_ROW:
            # samakan nilai lama ke setiap row lalu ubah jadi per-row
            for r in self.rows:
                r.setdefault(name, col.default if col.storage == FLAG_DEFAULT else _zero(col.type))
            col.storage = FLAG_ROW
            col.default = None
        self.rows[row][name] = value

    # ------------------------------------------------------------ parsing
    @classmethod
    def parse(cls, buf: bytes, offset: int = 0) -> "UtfTable":
        if buf[offset:offset + 4] != b"@UTF":
            raise UtfError("bukan tabel @UTF")
        (size,) = struct.unpack_from(">I", buf, offset + 4)
        base = offset + 8
        if base + size > len(buf):
            raise UtfError("tabel @UTF terpotong")
        version, rows_off = struct.unpack_from(">HH", buf, base)
        str_off, data_off, name_off = struct.unpack_from(">III", buf, base + 4)
        ncols, row_width, nrows = struct.unpack_from(">HHI", buf, base + 16)
        rows_off += base
        str_off += base
        data_off += base

        def read_str(o: int) -> str:
            start = str_off + o
            end = buf.index(b"\x00", start)
            raw = buf[start:end]
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("shift_jis", errors="replace")

        def read_val(t: int, pos: int) -> Any:
            if t in _FMT:
                return struct.unpack_from(_FMT[t], buf, pos)[0]
            if t == T_STR:
                return read_str(struct.unpack_from(">I", buf, pos)[0])
            if t == T_DATA:
                o, n = struct.unpack_from(">II", buf, pos)
                return bytes(buf[data_off + o:data_off + o + n])
            raise UtfError(f"tipe kolom tidak dikenal: {t:#x}")

        pos = base + 24
        columns: list[Column] = []
        for _ in range(ncols):
            flags = buf[pos]
            pos += 1
            name = ""
            if flags & FLAG_NAME:
                name = read_str(struct.unpack_from(">I", buf, pos)[0])
                pos += 4
            t = flags & 0x0F
            if t not in _SIZE:
                raise UtfError(f"tipe kolom tidak dikenal: {t:#x}")
            storage = flags & (FLAG_DEFAULT | FLAG_ROW)
            default = None
            if storage == FLAG_DEFAULT:
                default = read_val(t, pos)
                pos += _SIZE[t]
            columns.append(Column(name, t, storage, default))

        rows = []
        for r in range(nrows):
            p = rows_off + r * row_width
            row: dict[str, Any] = {}
            for c in columns:
                if c.storage == FLAG_ROW:
                    row[c.name] = read_val(c.type, p)
                    p += _SIZE[c.type]
                elif c.storage == FLAG_DEFAULT:
                    row[c.name] = c.default
                else:
                    row[c.name] = _zero(c.type)
            rows.append(row)
        return cls(read_str(name_off), columns, rows, version)

    # ------------------------------------------------------------ writing
    def to_bytes(self) -> bytes:
        strings = bytearray()
        str_index: dict[str, int] = {}

        def add_str(s: str) -> int:
            if s not in str_index:
                str_index[s] = len(strings)
                strings.extend(s.encode("utf-8") + b"\x00")
            return str_index[s]

        add_str("<NULL>")
        name_off = add_str(self.name)
        for c in self.columns:
            add_str(c.name)

        data = bytearray()

        def add_data(b: bytes) -> tuple[int, int]:
            if not b:
                return 0, 0
            off = len(data)
            data.extend(b)
            return off, len(b)

        def pack(t: int, v: Any) -> bytes:
            if t in _FMT:
                if t in (T_F32, T_F64):
                    return struct.pack(_FMT[t], float(v or 0))
                bits = _SIZE[t] * 8
                iv = int(v or 0) & ((1 << bits) - 1)
                if t in (T_S8, T_S16, T_S32, T_S64) and iv >= 1 << (bits - 1):
                    iv -= 1 << bits
                return struct.pack(_FMT[t], iv)
            if t == T_STR:
                return struct.pack(">I", add_str(v or ""))
            if t == T_DATA:
                return struct.pack(">II", *add_data(v or b""))
            raise UtfError(f"tipe kolom tidak dikenal: {t:#x}")

        schema = bytearray()
        for c in self.columns:
            schema.append(FLAG_NAME | c.storage | c.type)
            schema += struct.pack(">I", add_str(c.name))
            if c.storage == FLAG_DEFAULT:
                schema += pack(c.type, c.default)

        row_cols = [c for c in self.columns if c.storage == FLAG_ROW]
        row_width = sum(_SIZE[c.type] for c in row_cols)
        rows = bytearray()
        for r in self.rows:
            for c in row_cols:
                rows += pack(c.type, r.get(c.name))

        header_len = 24
        rows_off = header_len + len(schema)
        str_off = rows_off + len(rows)
        data_off = str_off + len(strings)
        body = bytearray()
        body += struct.pack(">HH", self.version, rows_off)
        body += struct.pack(">III", str_off, data_off, name_off)
        body += struct.pack(">HHI", len(self.columns), row_width, len(self.rows))
        body += schema + rows + strings + data
        while len(body) % 8:
            body.append(0)
        return b"@UTF" + struct.pack(">I", len(body)) + bytes(body)


def _zero(t: int) -> Any:
    if t == T_STR:
        return ""
    if t == T_DATA:
        return b""
    if t in (T_F32, T_F64):
        return 0.0
    return 0
