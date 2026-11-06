#!/usr/bin/env python3
"""Unit tests for the AArch64 instruction encoders in tools/asm.py, without game files.

Checks that each encoder disassembles (through capstone) to the expected instruction, and that the
branch range and alignment guard (check_branch_range) rejects misaligned and out-of-range displacements while
accepting the largest in-range ones. Needs capstone (the generator already imports it).

Run: python3 -I tests/test_encoders.py
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, finish  # noqa: E402
import asm  # noqa: E402
import build_patch as bp  # noqa: E402
from capstone import CS_ARCH_ARM64, CS_MODE_ARM, Cs  # noqa: E402

DISASSEMBLER = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
B_RANGE = 1 << 27             # b: signed 26-bit word displacement = +-128 MB
CBZ_RANGE = 1 << 20           # cbz: signed 19-bit word displacement = +-1 MB

MEMORY_ENCODINGS = [
    (asm.ldr_x(0, 1, 0x60), 'ldr x0, [x1, #0x60]', 'ldr_x'),
    (asm.str_x(9, 16, 0x48), 'str x9, [x16, #0x48]', 'str_x'),
    (asm.ldr_w(17, 19, 0x574), 'ldr w17, [x19, #0x574]', 'ldr_w'),
    (asm.str_w(17, 16, 0x40), 'str w17, [x16, #0x40]', 'str_w'),
    (asm.ldrb_w(17, 16, 0xF8), 'ldrb w17, [x16, #0xf8]', 'ldrb_w'),
    (asm.strb_w(17, 16, 0xFC), 'strb w17, [x16, #0xfc]', 'strb_w'),
    (asm.ldrh_w(17, 0, 0x7e), 'ldrh w17, [x0, #0x7e]', 'ldrh_w'),
    (asm.strh_w(31, 19, 0x562), 'strh wzr, [x19, #0x562]', 'strh_w'),
    (asm.movz_w(10, 0x80), 'mov w10, #0x80', 'movz_w'),
    (asm.cmp_i(17, 1), 'cmp w17, #1', 'cmp_i'),
]

# (word, address it sits at, expected disassembly, label)
BRANCH_ENCODINGS = [
    (asm.b(0x1000, 0x2000), 0x1000, 'b #0x2000', 'b fwd'),
    (asm.cbz_w(17, 0x100, 0x200), 0x100, 'cbz w17, #0x200', 'cbz_w'),
    (asm.cbnz_x(9, 0x100, 0x80), 0x100, 'cbnz x9, #0x80', 'cbnz_x back'),
    # a long in-range redirect (hook site -> cave) must encode, like the real build
    (asm.b(bp.HOOK_A, bp.CAVE_BASE), bp.HOOK_A, 'b #%s' % hex(bp.CAVE_BASE), 'b hook->cave'),
]


def disassemble(word, address=0):
    instruction = next(DISASSEMBLER.disasm(struct.pack('<I', word), address))
    return ('%s %s' % (instruction.mnemonic, instruction.op_str)).strip()


def raises_assertion(encode):
    try:
        encode()
    except AssertionError:
        return True
    return False


def test_memory_and_data_encoders():
    for word, want, label in MEMORY_ENCODINGS:
        got = disassemble(word)
        check(label, got == want, 'got %r want %r' % (got, want))


def test_branch_targets():
    for word, address, want, label in BRANCH_ENCODINGS:
        got = disassemble(word, address)
        check(label, got == want, 'got %r want %r' % (got, want))


def test_branch_range_guard():
    check('b misaligned', raises_assertion(lambda: asm.b(0, 2)), 'expected AssertionError, none raised')
    check('b out of range (+128MB)', raises_assertion(lambda: asm.b(0, B_RANGE)), 'expected AssertionError, none raised')
    check('cbz out of range (+1MB)', raises_assertion(lambda: asm.cbz_w(0, 0, CBZ_RANGE)),
          'expected AssertionError, none raised')
    check('b max in range', not raises_assertion(lambda: asm.b(0, B_RANGE - 4)), 'in-range branch was rejected')
    check('cbz max in range', not raises_assertion(lambda: asm.cbz_w(0, 0, CBZ_RANGE - 4)), 'in-range cbz was rejected')


def main():
    test_memory_and_data_encoders()
    test_branch_targets()
    test_branch_range_guard()
    finish('all encoder + branch-range checks passed')


if __name__ == '__main__':
    main()
