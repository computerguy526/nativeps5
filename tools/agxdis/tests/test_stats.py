#!/usr/bin/env python3
"""Unit tests for the agxdis stats parser (no compiler, no GPU): synthetic instruction lists."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import agxdis  # noqa: E402


def I(off, text, src="decoder", n=4):
    return agxdis.Ins(off, b"\x00" * n, text.split()[0], text, src)


class MemWidth(unittest.TestCase):
    def test_widths(self):
        self.assertEqual(agxdis.mem_width("device_load 5, i32, xyzw, r0_r1_r2_r3, u0_u1, r0.discard, lsl 2"), ("i32x4", 16))
        self.assertEqual(agxdis.mem_width("device_load 5, i32, x, r0, u0_u1, r0"), ("i32x1", 4))
        self.assertEqual(agxdis.mem_width("device_load 5, i16, xyzw, r0l_r0h_r1l_r1h, u0_u1, r0"), ("i16x4", 8))
        self.assertEqual(agxdis.mem_width("device_store 0, i32, xy, r0_r1, u2_u3, r4"), ("i32x2", 8))
        self.assertEqual(agxdis.mem_width("device_load 0, i8, xyz, r0, u2_u3, r4"), ("i8x3", 3))
        self.assertEqual(agxdis.mem_width("device_load  <agx-re name/length only>"), ("?", 0))


class Stats(unittest.TestCase):
    def st(self, ins, isa="g15", trunc=0):
        return agxdis.stats(ins, trunc, sum(len(i.raw) for i in ins) + trunc, isa)

    def test_registers(self):
        s = self.st([I(0, "get_sr 0, r4, sr32 (thread_position_in_grid.x)"),
                     I(4, "device_load 5, i32, xyzw, r0_r1_r2_r3, u0_u1, r0.discard, lsl 2"),
                     I(8, "fmul cr6, 0.5, r9l.discard, group 0"),
                     I(12, "stop")])
        self.assertEqual((s["gpr"], s["gpr_max"]), (6, 9))   # r0-r4, r9; sr32 and cr6 are not GPRs
        self.assertEqual((s["ureg"], s["creg"]), (2, 1))
        self.assertEqual((s["instr"], s["bytes"]), (4, 16))

    def test_loads_by_width(self):
        s = self.st([I(0, "device_load 5, i32, xyzw, r0_r1_r2_r3, u0_u1, r0"),
                     I(4, "device_load 5, i32, x, r5, u0_u1, r0"),
                     I(8, "device_load 5, i32, x, r6, u0_u1, r0"),
                     I(12, "device_store 0, i16, xy, r0l_r0h, u2_u3, r4")])
        self.assertEqual((s["dload"], s["dload_bytes"], s["dload_w"]), (3, 24, {"i32x4": 1, "i32x1": 2}))
        self.assertEqual((s["dstore"], s["dstore_bytes"], s["dstore_w"]), (1, 4, {"i16x2": 1}))
        self.assertEqual(agxdis.fmt_w(s["dload_w"]), "i32x1:2,i32x4:1")

    def test_loops_and_branches(self):
        s = self.st([I(0x00, "if cc0.neg, 1"), I(0x04, "jmp_exec_none 0x40"),
                     I(0x10, "push_exec 2"), I(0x20, "while cc0.neg, 2"), I(0x24, "jmp_exec_any 0x10"),
                     I(0x30, "while.todo cc0.neg, 2"), I(0x34, "jmp_exec_any 0x28"),
                     I(0x38, "else.todo cc_always, 1"), I(0x40, "pop_exec 1")])
        self.assertEqual((s["loops"], s["while"], s["if"], s["else"], s["jmp"]), (2, 2, 1, 1, 3))

    def test_same_loop_two_back_edges(self):
        s = self.st([I(0x20, "jmp_exec_any 0x10"), I(0x30, "jmp_exec_any 0x10")])
        self.assertEqual(s["loops"], 1)

    def test_spill_slots(self):
        s = self.st([I(0, "mov st12, r2.discard, group 0"), I(4, "fmul st331, r84.discard, r78.discard, group 2"),
                     I(8, "get_stack 5, 5, r10, st12"), I(12, "get_stack 5, 5, r11, st331, wait 5"),
                     I(16, "bitop 0000, st24, r0l, r0l, group 4"), I(20, "fadd r1, r2, r3")])
        self.assertEqual((s["spill_ld"], s["spill_st"], s["spill_slots"]), (2, 3, 3))
        self.assertIn("spill=1", agxdis.stats_line("compute", "main", "applegpu_g16s", "g15", s))

    def test_stack_memory(self):
        s = self.st([I(0, "stack_store  <operands not decoded>", "table"), I(4, "stack_load  <operands not decoded>", "table"),
                     I(8, "stack_load r7, i32, 1, 0, x, 4, r7, 0")])
        self.assertEqual((s["stack_st"], s["stack_ld"], s["partial"]), (1, 2, 2))
        self.assertIn(" spill=1 ", agxdis.stats_line("compute", "main", "a", "g15", s))

    def test_no_spill(self):
        s = self.st([I(0, "fadd r1, r2, r3"), I(4, "stop")])
        self.assertIn(" spill=0 ", agxdis.stats_line("compute", "main", "a", "g15", s))

    def test_texture_names_per_isa(self):
        ins = [I(0, "texture_load 0, 0, xyzw, r0_r1_r2_r3, u0_u1, 0, u2_u3, 0, cr0_cr1.discard, 2d, kill"),
               I(4, "texture_sample 0, 0, xyzw, r0_r1_r2_r3, u0_u1, 0, u4_u5, 0, cr0_cr1.discard, 2d level(0)"),
               I(8, "texture_sample 0, 0, xyzw, r0_r1_r2_r3, u0_u1, 0, u4_u5, 0, cr0_cr1.discard, 2d level(0)"),
               I(12, "image_store  <operands not decoded>", "table")]
        s = self.st(ins, "g15")
        self.assertEqual((s["tex_sample"], s["tex_load"], s["tex_store"]), (1, 2, 1))
        s = self.st(ins, "g13")
        self.assertEqual((s["tex_sample"], s["tex_load"]), (2, 1))

    def test_unsure_operands_ignored(self):
        s = self.st([I(0, "cmpsel.todo ieq, r304, r55h.cache, 0, r77, 0.0"), I(4, "fadd r1, r2, r3")])
        self.assertEqual((s["unsure"], s["gpr"], s["gpr_max"]), (1, 3, 3))

    def test_partial_unknown_trunc(self):
        s = self.st([I(0, "atomic_mem  <agx-re name/length only>", "agx-re"), I(4, "<unknown>", "unknown"),
                     I(8, "vertex_store  <operands not decoded>", "table")], trunc=6)
        self.assertEqual((s["partial"], s["unknown"], s["atomic"], s["trunc"], s["bytes"]), (2, 1, 1, 6, 18))
        self.assertTrue(agxdis.stats_line("c", "m", "a", "g15", s).endswith("trunc=6 complete=0"))


class Sniff(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(agxdis.sniff(b"MTLB\x01\x80"), "metallib")
        self.assertEqual(agxdis.sniff(b"\xcb\xfe\xba\xbe\x00\x00\x00\x02"), "archive")
        self.assertEqual(agxdis.sniff(b"\xcf\xfa\xed\xfe\x13\x00\x00\x01"), "archive")
        self.assertEqual(agxdis.sniff(b"#include <metal_stdlib>\nkernel void k() {}\n"), "msl")
        self.assertEqual(agxdis.sniff(b"\x00\x01\x02\xff\xfe binary"), "unknown")

    def test_arch_table(self):
        import struct
        thin = struct.pack("<III", 0xFEEDFACF, 0x01000013, 467) + b"\x00" * 20
        self.assertEqual(agxdis.archive_arch(thin), (467, "applegpu_g16s"))
        self.assertEqual(agxdis.archive_arch(struct.pack("<III", 0xFEEDFACF, 0x01000013, 999) + b"\x00" * 20), (999, None))
        with self.assertRaises(agxdis.Fail) as c:
            agxdis.archive_arch(struct.pack("<III", 0xFEEDFACF, 0x0100000C, 0) + b"\x00" * 20)
        self.assertEqual(c.exception.code, 10)
        self.assertEqual(agxdis.gen_of("applegpu_g13g"), 13)
        self.assertEqual(agxdis.ISA_OF_GEN.get(agxdis.gen_of("applegpu_g17p")), None)


class Stub(unittest.TestCase):
    SRC = """struct FragmentIn { float4 vary_00 [[user(vary_00)]] ; float2 vary_06 [[user(vary_06)]] ; float4 color_0 [[color(0)]]; };
struct FragmentOut { float4 color_0 [[color(0)]]; uint4 color_2 [[color(2)]]; float d [[depth(any)]]; };
fragment FragmentOut main_entrypoint(float4 gl_FragCoord [[position]], FragmentIn in [[stage_in]]) { FragmentOut o = {}; return o; }
"""

    def test_fragment_only(self):
        stub, colors, depth = agxdis.fragment_interface(self.SRC, "rgba16float")
        self.assertIn("float4 vary_00 [[user(vary_00)]];", stub)
        self.assertIn("float2 vary_06 [[user(vary_06)]];", stub)
        self.assertNotIn("color_0", stub)
        self.assertIn("vertex agxdis_VOut agxdis_vs", stub)
        self.assertEqual(colors, {0: "rgba16float", 2: "rgba8uint"})
        c2 = agxdis.fragment_interface("struct O { float a [[color(0)]]; float2 b [[color(1)]]; uint c [[color(3)]]; };\n"
                                       "fragment O f() { O o; return o; }", "rgba8unorm")[1]
        self.assertEqual(c2, {0: "r8unorm", 1: "rg8unorm", 3: "r32uint"})
        self.assertTrue(depth)

    def test_no_stub_when_vertex_present_or_kernel(self):
        self.assertIsNone(agxdis.fragment_interface(self.SRC + "\nvertex float4 vs() { return float4(0); }\n", "rgba8unorm")[0])
        self.assertIsNone(agxdis.fragment_interface("kernel void k() {}", "rgba8unorm")[0])
        self.assertIsNone(agxdis.fragment_interface("// fragment float4 f() in a comment\nkernel void k() {}", "rgba8unorm")[0])


if __name__ == "__main__":
    unittest.main(verbosity=1)
