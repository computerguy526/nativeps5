#!/usr/bin/env python3
"""Tests for bmp.py and pics.py on tiny synthetic BMP pairs with known differences."""
import contextlib, io, os, struct, sys, tempfile, unittest, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evfix  # noqa: E402
import bmp  # noqa: E402
import pics  # noqa: E402


def img(w, h, f, bpp=24, top_down=False):
    return bmp.parse_bmp(bmp.make_bmp(w, h, f, bpp, top_down))


def gray(v):
    return (v, v, v)


class Reader(unittest.TestCase):
    def pix(self, x, y):
        return (x * 10, y * 20, 7)

    def test_layouts_agree(self):
        a = img(3, 2, self.pix, 24)                     # 3 px x 3 B = 9 B rows, padded to 12
        b = img(3, 2, self.pix, 24, top_down=True)
        c = img(3, 2, self.pix, 32)
        for im in (a, b, c):
            self.assertEqual((im.w, im.h), (3, 2))
            self.assertEqual(im.pixel(2, 1), (20, 20, 7))
            self.assertEqual(im.pixel(0, 0), (0, 0, 7))
        self.assertEqual(a.rows, b.rows)

    def test_rejects_bad_files(self):
        good = bmp.make_bmp(2, 2, lambda x, y: gray(0))
        for d in (b"", b"PNG" + b"\0" * 80, good[:60], good[:28] + struct.pack("<H", 8) + good[30:]):
            with self.assertRaises(bmp.BmpError):
                bmp.parse_bmp(d)


class Metrics(unittest.TestCase):
    def test_identical(self):
        a, b = img(8, 8, lambda x, y: (x, y, 3)), img(8, 8, lambda x, y: (x, y, 3), 32)
        d = bmp.diff_metrics(a, b)
        self.assertTrue(d["identical"])

    def test_known_diff(self):
        a = img(8, 8, lambda x, y: gray(100))
        b = img(8, 8, lambda x, y: (110, 100, 100))            # +10 in red only
        d = bmp.diff_metrics(a, b)
        self.assertFalse(d["identical"])
        self.assertAlmostEqual(d["mad"], 10 / 3)
        self.assertEqual(d["max"], 10)
        self.assertAlmostEqual(d["frac"], 1 / 3)               # one channel in three exceeds 2
        self.assertEqual(bmp.diff_metrics(a, img(4, 4, lambda x, y: gray(0))), None)

    def test_small_diffs_below_threshold_are_not_counted(self):
        a, b = img(4, 4, lambda x, y: gray(10)), img(4, 4, lambda x, y: gray(12))
        d = bmp.diff_metrics(a, b)
        self.assertEqual(d["frac"], 0.0)
        self.assertAlmostEqual(d["mad"], 2.0)

    def test_large_image_sampling_is_exact_for_uniform_shift(self):
        a = img(600, 500, lambda x, y: gray(50))
        b = img(600, 500, lambda x, y: gray(58))
        d = bmp.diff_metrics(a, b, max_px=10_000)              # sampled on a grid
        self.assertAlmostEqual(d["mad"], 8.0)
        self.assertAlmostEqual(d["frac"], 1.0)
        self.assertEqual(len(bmp.luma(a, 10_000)) * len(bmp.luma(a, 10_000)[0]) <= 10_000, True)

    def test_region_heat(self):
        a = img(8, 8, lambda x, y: gray(100))
        b = img(8, 8, lambda x, y: gray(140) if x < 4 and y < 4 else gray(100))
        g = bmp.region_diff(a, b, 2)
        self.assertEqual([[round(v, 6) for v in r] for r in g], [[40.0, 0.0], [0.0, 0.0]])
        self.assertIsNone(bmp.region_diff(a, img(4, 4, lambda x, y: gray(0)), 2))
        heat = bmp.heat_rgb(g, 8, 8)
        self.assertEqual(len(heat), 8)
        self.assertGreater(heat[0][0], 0)                                # hot corner is lit
        self.assertEqual(heat[7][-3:], b"\0\0\0")                       # cold corner is black

    def test_flicker_and_sharpness(self):
        flat = img(8, 8, lambda x, y: gray(80))
        self.assertEqual(bmp.flicker(flat, flat)["mean"], 0.0)
        blink = img(8, 8, lambda x, y: gray(112))
        f = bmp.flicker(flat, blink)
        self.assertAlmostEqual(f["mean"], 32.0)
        self.assertEqual(f["over16"], 1.0)
        near = img(8, 8, lambda x, y: gray(90))
        self.assertEqual(bmp.flicker(flat, near)["over16"], 0.0)
        stripes = img(8, 8, lambda x, y: gray(255 if x % 2 else 0))
        self.assertAlmostEqual(bmp.sharpness(bmp.luma(stripes)), 255.0, places=3)
        self.assertEqual(bmp.sharpness(bmp.luma(flat)), 0.0)
        self.assertIsNone(bmp.flicker(flat, img(4, 4, lambda x, y: gray(0))))


class Png(unittest.TestCase):
    def test_png_roundtrip(self):
        rows = [bytes([1, 2, 3, 4, 5, 6]), bytes([7, 8, 9, 10, 11, 12])]
        p = bmp.png_bytes(2, 2, rows)
        self.assertEqual(p[:8], b"\x89PNG\r\n\x1a\n")
        i, idat = 8, b""
        while i < len(p):
            n, tag = struct.unpack(">I4s", p[i:i + 8])
            body = p[i + 8:i + 8 + n]
            self.assertEqual(struct.unpack(">I", p[i + 8 + n:i + 12 + n])[0], zlib.crc32(tag + body) & 0xFFFFFFFF)
            if tag == b"IHDR":
                self.assertEqual(struct.unpack(">IIBBBBB", body), (2, 2, 8, 2, 0, 0, 0))
            if tag == b"IDAT":
                idat += body
            i += 12 + n
        raw = zlib.decompress(idat)
        self.assertEqual(raw, b"\0" + rows[0] + b"\0" + rows[1])


class Pairing(unittest.TestCase):
    def test_pairs_by_present_count(self):
        with tempfile.TemporaryDirectory() as td:
            a, b = os.path.join(td, "a"), os.path.join(td, "b")
            os.makedirs(a), os.makedirs(b)
            data = bmp.make_bmp(2, 2, lambda x, y: gray(0))
            for n in (100, 500):
                evfix.write(os.path.join(a, f"frame_{n:06d}.bmp"), data)
            for n in (100, 300):
                evfix.write(os.path.join(b, f"frame_{n:06d}.bmp"), data)
            pa, oa, ob = pics.pair_dumps(pics.list_dumps(a), pics.list_dumps(b))
            self.assertEqual([p[1] for p in pa], [100])
            self.assertEqual((len(oa), len(ob)), (1, 1))
            self.assertEqual(pics.pair_dumps(pics.list_dumps(a), pics.list_dumps(b), {500}) [0], [])

    def test_flicker_series(self):
        with tempfile.TemporaryDirectory() as td:
            for n, v in ((1, 80), (2, 80), (3, 112)):
                evfix.write(os.path.join(td, f"k3_{n}_abc_8x8_bgra.bmp"), bmp.make_bmp(8, 8, lambda x, y, v=v: gray(v)))
            s = pics.flicker_series(pics.list_dumps(td))
            (k, f), = s.items()
            self.assertEqual(f["dumps"], 3)
            self.assertAlmostEqual(f["flicker_mean"], 16.0)        # pairs: 0 and 32
            self.assertAlmostEqual(f["flicker_over16"], 0.5)


class Cli(unittest.TestCase):
    def run_cli(self, args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = pics.main(args)
        return rc, out.getvalue(), err.getvalue()

    def test_end_to_end(self):
        with tempfile.TemporaryDirectory() as td:
            ra, rb = evfix.make_run(td, "T", "a", [20.0] * 10), evfix.make_run(td, "T", "b", [20.0] * 10)
            for d, v in ((ra, 100), (rb, 130)):
                for n in (100, 200):
                    evfix.write(os.path.join(d, "frames", f"frame_{n}.bmp"), bmp.make_bmp(16, 16, lambda x, y, v=v: gray(v)))
            sheet = os.path.join(td, "sheet.png")
            rc, out, _ = self.run_cli([ra, rb, "--out", sheet, "--max", "1"])
            self.assertEqual(rc, 0)
            self.assertIn("frame_#.bmp @100: mad 30.000 max 30 frac>2 1.0000", out)
            self.assertIn("flicker A", out)
            with open(sheet, "rb") as fh:
                self.assertEqual(fh.read(8), b"\x89PNG\r\n\x1a\n")
            rc, out, _ = self.run_cli([ra, ra])
            self.assertIn("identical", out)
            rc, _, err = self.run_cli([ra, os.path.join(td, "T", "missing")])
            self.assertEqual(rc, 2)
            empty = evfix.make_run(td, "T", "c", [20.0] * 10)
            self.assertEqual(self.run_cli([ra, empty])[0], 1)             # no dumps in common
        self.assertEqual(self.run_cli([])[0], 2)
        self.assertEqual(self.run_cli(["a"])[0], 2)
        self.assertEqual(self.run_cli(["a", "b", "--nope"])[0], 2)

    def test_size_mismatch_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            ra, rb = evfix.make_run(td, "T", "a", [20.0] * 10), evfix.make_run(td, "T", "b", [20.0] * 10)
            evfix.write(os.path.join(ra, "frames", "frame_1.bmp"), bmp.make_bmp(4, 4, lambda x, y: gray(0)))
            evfix.write(os.path.join(rb, "frames", "frame_1.bmp"), bmp.make_bmp(8, 8, lambda x, y: gray(0)))
            rc, out, _ = self.run_cli([ra, rb, "--out", os.path.join(td, "s.png")])
            self.assertEqual(rc, 0)
            self.assertIn("SIZE MISMATCH", out)


if __name__ == "__main__":
    unittest.main()
