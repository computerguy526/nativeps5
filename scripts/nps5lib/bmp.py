"""One reader for the BMP frame/target dumps, plus the picture metrics and a stdlib PNG writer.
Reads uncompressed 24/32-bit BMP (bottom-up or top-down, row padding handled). Pure Python: large images are
sampled on a regular grid (at most `max_px` pixels) instead of read pixel by pixel, so a metric is an estimate with
a known cap on cost; identical files still compare exactly equal. Pillow is not needed (and not used)."""
from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass
from typing import Optional


class BmpError(ValueError):
    pass


@dataclass
class Image:
    """Top-down rows of B,G,R[,A] bytes (no padding)."""
    w: int
    h: int
    bpp: int
    rows: list[bytes]

    @property
    def cpp(self) -> int:
        return self.bpp // 8

    def pixel(self, x: int, y: int) -> tuple[int, int, int]:
        r = self.rows[y]
        o = x * self.cpp
        return r[o + 2], r[o + 1], r[o]


def parse_bmp(d: bytes) -> Image:
    if len(d) < 54 or d[:2] != b"BM":
        raise BmpError("not a BMP")
    off, hdr = struct.unpack_from("<II", d, 10)
    if hdr < 40:
        raise BmpError("unsupported BMP header")
    w, h = struct.unpack_from("<ii", d, 18)
    bpp = struct.unpack_from("<H", d, 28)[0]
    comp = struct.unpack_from("<I", d, 30)[0]
    if bpp not in (24, 32) or comp not in (0, 3) or w <= 0 or h == 0:
        raise BmpError(f"unsupported BMP (bpp {bpp}, compression {comp})")
    if off < 14 + hdr:                                   # off=0 would read the header as pixels
        raise BmpError("bad pixel data offset")
    if comp == 3:                                        # BITFIELDS: only the default BGR(A) byte order is read as such
        if bpp != 32 or off < 14 + 40 + 12 or len(d) < 14 + 40 + 12:
            raise BmpError("unsupported BMP (bitfields need 32 bpp and the colour masks before the pixels)")
        mr, mg, mb = struct.unpack_from("<III", d, 54)
        ma = struct.unpack_from("<I", d, 66)[0] if hdr >= 56 and off >= 70 else 0
        if (mr, mg, mb) != (0xFF0000, 0xFF00, 0xFF) or ma not in (0, 0xFF000000):
            raise BmpError("unsupported BMP (bitfield masks are not the default BGR(A) layout)")
    top_down = h < 0
    h = abs(h)
    rb = w * (bpp // 8)
    stride = (rb + 3) // 4 * 4
    if len(d) < off + stride * (h - 1) + rb:
        raise BmpError("truncated BMP")
    rows = [d[off + i * stride: off + i * stride + rb] for i in range(h)]
    if not top_down:
        rows.reverse()
    return Image(w, h, bpp, rows)


def read_bmp(path: str) -> Image:
    with open(path, "rb") as f:
        return parse_bmp(f.read())


def make_bmp(w: int, h: int, pix, bpp: int = 24, top_down: bool = False) -> bytes:
    """Build a BMP from pix(x, y) -> (r, g, b). Used by tests and by the tools' own self-checks."""
    cpp = bpp // 8
    stride = (w * cpp + 3) // 4 * 4
    body = []
    for y in range(h):
        row = bytearray()
        for x in range(w):
            r, g, b = pix(x, y)
            row += bytes((b, g, r, 255)[:cpp])
        row += b"\0" * (stride - len(row))
        body.append(bytes(row))
    if not top_down:
        body.reverse()
    data = b"".join(body)
    hdr = struct.pack("<2sIHHI", b"BM", 54 + len(data), 0, 0, 54) + struct.pack("<IiiHHIIiiII", 40, w, -h if top_down else h, 1, bpp, 0, len(data), 2835, 2835, 0, 0)
    return hdr + data


def _grid(w: int, h: int, max_px: int) -> tuple[range, range]:
    s = max(1, int(math.ceil(math.sqrt(w * h / max_px))))
    return range(0, h, s), range(0, w, s)


def luma(img: Image, max_px: int = 250_000) -> list[list[float]]:
    """Luma (BT.601, 0-255) on the sampling grid, rows top-down."""
    ys, xs = _grid(img.w, img.h, max_px)
    c = img.cpp
    out = []
    for y in ys:
        r = img.rows[y]
        out.append([0.114 * r[x * c] + 0.587 * r[x * c + 1] + 0.299 * r[x * c + 2] for x in xs])
    return out


def sharpness(y: list[list[float]]) -> float:
    """Mean |d/dx| + mean |d/dy| of a luma grid (8-bit units)."""
    if len(y) < 2 or len(y[0]) < 2:
        return 0.0
    dx = sum(abs(r[i + 1] - r[i]) for r in y for i in range(len(r) - 1)) / (len(y) * (len(y[0]) - 1))
    dy = sum(abs(y[j + 1][i] - y[j][i]) for j in range(len(y) - 1) for i in range(len(y[0]))) / ((len(y) - 1) * len(y[0]))
    return dx + dy


def _same_rgb(a: Image, b: Image) -> bool:
    """Exact equality of the R, G, B bytes of every pixel (alpha and row layout ignored)."""
    if a.bpp == b.bpp:
        if a.rows == b.rows:
            return True
        if a.cpp == 3:
            return False
    ca, cb = a.cpp, b.cpp
    return all(ra[k::ca][:a.w] == rb[k::cb][:a.w] for ra, rb in zip(a.rows, b.rows) for k in range(3))


def diff_metrics(a: Image, b: Image, threshold: int = 2, max_px: int = 250_000) -> Optional[dict]:
    """Per-channel (RGB) comparison: identical, mean abs diff (0-255), max, fraction of channel samples differing by
    more than `threshold`. None if the images differ in size. identical is True only for exactly equal pixels; if the
    sample sees no difference but the pixels differ, identical is False and sample_miss is True (mad/max are then 0 = unknown)."""
    if (a.w, a.h) != (b.w, b.h):
        return None
    if _same_rgb(a, b):                                  # exact, full compare: only this may say identical
        return dict(identical=True, mad=0.0, max=0, frac=0.0)
    ys, xs = _grid(a.w, a.h, max_px)
    s = n = cnt = mx = 0
    for y in ys:
        ra, rb = a.rows[y], b.rows[y]
        for x in xs:
            for k in range(3):
                d = abs(ra[x * a.cpp + k] - rb[x * b.cpp + k])
                s += d
                n += 1
                mx = max(mx, d)
                cnt += d > threshold
    # the full compare above failed: a clean sample means the difference lies between grid points, never "identical"
    return dict(identical=False, mad=s / n, max=mx, frac=cnt / n, **(dict(sample_miss=True) if mx == 0 else {}))


def region_diff(a: Image, b: Image, regions: int = 8, max_px: int = 250_000) -> Optional[list[list[float]]]:
    """regions x regions grid of mean abs luma difference (0-255); None on a size mismatch."""
    if (a.w, a.h) != (b.w, b.h):
        return None
    ya, yb = luma(a, max_px), luma(b, max_px)
    H, W = len(ya), len(ya[0])
    out = []
    for gy in range(regions):
        y0, y1 = gy * H // regions, max(gy * H // regions + 1, (gy + 1) * H // regions)
        row = []
        for gx in range(regions):
            x0, x1 = gx * W // regions, max(gx * W // regions + 1, (gx + 1) * W // regions)
            t = c = 0
            for y in range(y0, min(y1, H)):
                for x in range(x0, min(x1, W)):
                    t += abs(ya[y][x] - yb[y][x])
                    c += 1
            row.append(t / c if c else 0.0)
        out.append(row)
    return out


def flicker(prev: Image, cur: Image, max_px: int = 250_000) -> Optional[dict]:
    """Change between two consecutive frames: mean |luma diff| and the share of samples differing by > 16/255."""
    if (prev.w, prev.h) != (cur.w, cur.h):
        return None
    p, c = luma(prev, max_px), luma(cur, max_px)
    n = sum(len(r) for r in p)
    tot = big = 0.0
    for rp, rc in zip(p, c):
        for u, v in zip(rp, rc):
            d = abs(u - v)
            tot += d
            big += d > 16
    return dict(mean=tot / n, over16=big / n)


# ---------------------------------------------------------------- PNG output
def _chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def png_bytes(w: int, h: int, rows_rgb: list[bytes]) -> bytes:
    raw = b"".join(b"\0" + r for r in rows_rgb)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b"")


def thumb_rgb(img: Image, width: int) -> tuple[int, int, list[bytes]]:
    """Nearest-neighbour thumbnail as RGB rows."""
    tw = min(width, img.w)
    th = max(1, img.h * tw // img.w)
    c = img.cpp
    rows = []
    for ty in range(th):
        r = img.rows[ty * img.h // th]
        row = bytearray()
        for tx in range(tw):
            o = (tx * img.w // tw) * c
            row += bytes((r[o + 2], r[o + 1], r[o]))
        rows.append(bytes(row))
    return tw, th, rows


def heat_rgb(grid: list[list[float]], width: int, height: int, full_scale: float = 32.0) -> list[bytes]:
    """Region grid -> heat map (black -> red -> yellow -> white as the difference grows to full_scale)."""
    n = len(grid)
    rows = []
    for y in range(height):
        gy = min(n - 1, y * n // height)
        row = bytearray()
        for x in range(width):
            v = min(1.0, grid[gy][min(n - 1, x * n // width)] / full_scale)
            row += bytes((min(255, int(v * 3 * 255)), min(255, max(0, int((v * 3 - 1) * 255))), min(255, max(0, int((v * 3 - 2) * 255)))))
        rows.append(bytes(row))
    return rows
