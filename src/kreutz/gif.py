"""Animated GIF writer, standard library only (GIF89a, LZW, one global palette).

Frames are 8-bit index arrays. The palette is 254 greys (0..253 map 0..255)
plus index 254 = green and 255 = yellow for annotations, so a movie frame is
`gray_to_index(img)` with rings drawn in 254.
"""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

GREEN, YELLOW = 254, 255


def palette() -> bytes:
    greys = [int(round(i * 255 / 253)) for i in range(254)]
    return b"".join(bytes((g, g, g)) for g in greys) + bytes((79, 211, 122)) + bytes((255, 211, 77))


def gray_to_index(img: np.ndarray) -> np.ndarray:
    """0..255 grey -> palette index 0..253."""
    return np.clip(np.rint(img.astype(np.float32) * 253 / 255), 0, 253).astype(np.uint8)


def write_gif(path: Path, frames: list[np.ndarray], delay_cs: int = 35, loop: bool = True) -> None:
    h, w = frames[0].shape
    out = [b"GIF89a", struct.pack("<HHBBB", w, h, 0xF7, 0, 0), palette()]  # global colour table, 256 entries
    if loop:
        out.append(b"\x21\xFF\x0BNETSCAPE2.0\x03\x01\x00\x00\x00")
    for f in frames:
        assert f.shape == (h, w)
        out.append(b"\x21\xF9\x04\x00" + struct.pack("<H", delay_cs) + b"\x00\x00")  # graphic control: delay
        out.append(b"\x2C" + struct.pack("<HHHHB", 0, 0, w, h, 0))
        out.append(b"\x08" + _blocks(_lzw(f.tobytes(), 8)) + b"\x00")
    out.append(b"\x3B")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(out))


def _lzw(data: bytes, min_code_size: int) -> bytes:
    clear, eoi = 1 << min_code_size, (1 << min_code_size) + 1
    code_size = min_code_size + 1
    table: dict[bytes, int] = {bytes((i,)): i for i in range(clear)}
    next_code = eoi + 1
    bits, nbits, out = 0, 0, bytearray()

    def emit(code: int) -> None:
        nonlocal bits, nbits
        bits |= code << nbits
        nbits += code_size
        while nbits >= 8:
            out.append(bits & 0xFF)
            bits >>= 8
            nbits -= 8

    emit(clear)
    cur = b""
    for b in data:
        nxt = cur + bytes((b,))
        if nxt in table:
            cur = nxt
            continue
        emit(table[cur])
        if next_code < 4096:
            table[nxt] = next_code
            next_code += 1
            if next_code - 1 == (1 << code_size) and code_size < 12:
                code_size += 1
        else:
            emit(clear)
            table = {bytes((i,)): i for i in range(clear)}
            next_code, code_size = eoi + 1, min_code_size + 1
        cur = bytes((b,))
    if cur:
        emit(table[cur])
    emit(eoi)
    if nbits:
        out.append(bits & 0xFF)
    return bytes(out)


def _blocks(payload: bytes) -> bytes:
    return b"".join(bytes((len(payload[i:i + 255]),)) + payload[i:i + 255] for i in range(0, len(payload), 255))
