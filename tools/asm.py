#!/usr/bin/env python3
"""A small AArch64 assembler for the patch generator: instruction encoders and a two-pass label assembler.

Every function returns one 32-bit instruction word. Names follow the instruction, with a suffix for
the register width: `_w` = 32-bit (wN), `_x` = 64-bit (xN). Operands use the ARM names: `rd` is the
destination register, `rn`/`rm` source registers, `rt` the register a load or store transfers, `off` a
byte offset, `imm` an immediate. Register 31 means `sp` or `wzr`/`xzr`, as in the instruction set.
Branch encoders take the address of the branch (`cur`) and of its target (`t`), both as memory offsets,
and check that the distance fits the instruction's range.

`Asm` lays a code cave out in two passes so branches can name labels instead of counting words:

    a = Asm(base)
    a.cbz_w(17, 'skip')
    a.w(movz_w(9, 1))
    a.label('skip')
    words = a.words()
"""


def sign_extend(value, bits):
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def check_branch_range(cur, target, imm_bits):
    """Assert a branch displacement is 4-aligned and fits the signed imm*4 range of the instruction.
    imm_bits = 26 for b/bl (±128 MB), 19 for cbz/cbnz/b.cond (±1 MB), 14 for tbz/tbnz (±32 KB)."""
    distance = target - cur
    assert distance % 4 == 0, ("branch misaligned", hex(cur), hex(target), distance)
    low, high = -(1 << (imm_bits + 1)), (1 << (imm_bits + 1)) - 4
    assert low <= distance <= high, ("branch out of range", hex(cur), hex(target), distance, "imm%d" % imm_bits)
    return distance


# ----- condition codes (for bcond / csel) -----------------------------------------------------------
EQ, NE, HS, LO, HI, LS = 0, 1, 2, 3, 8, 9

# ----- fixed words ----------------------------------------------------------------------------------
RET = 0xD65F03C0
NOP = 0xD503201F
STP_1617, LDP_1617 = 0xA9BF47F0, 0xA8C147F0      # push / pop x16,x17:  stp x16,x17,[sp,#-0x10]! / ldp ...,[sp],#0x10
STP_910, LDP_910 = 0xA9BF2BE9, 0xA8C12BE9        # push / pop x9,x10


# ----- loads and stores (unsigned offset) -----------------------------------------------------------
def ldr_x(rt, rn, off):
    assert off % 8 == 0
    return 0xF9400000 | ((off // 8) << 10) | (rn << 5) | rt


def str_x(rt, rn, off):
    assert off % 8 == 0
    return 0xF9000000 | ((off // 8) << 10) | (rn << 5) | rt


def ldr_w(rt, rn, off):
    return 0xB9400000 | ((off // 4) << 10) | (rn << 5) | rt


def str_w(rt, rn, off):
    return 0xB9000000 | ((off // 4) << 10) | (rn << 5) | rt


def ldrh_w(rt, rn, off):
    return 0x79400000 | ((off // 2) << 10) | (rn << 5) | rt


def strh_w(rt, rn, off):
    return 0x79000000 | ((off // 2) << 10) | (rn << 5) | rt


def ldrb_w(rt, rn, off):
    return 0x39400000 | (off << 10) | (rn << 5) | rt


def strb_w(rt, rn, off):
    return 0x39000000 | (off << 10) | (rn << 5) | rt


def ldrsw_i(rt, rn, off):                        # ldrsw xt,[xn,#off]
    assert off % 4 == 0
    return 0xB9800000 | ((off // 4) << 10) | (rn << 5) | rt


# ----- loads and stores (unscaled, pre/post-index, register offset) ---------------------------------
def ldur_w(rt, rn, imm):
    return 0xB8400000 | ((imm & 0x1FF) << 12) | (rn << 5) | rt


def stur_w(rt, rn, imm):
    return 0xB8000000 | ((imm & 0x1FF) << 12) | (rn << 5) | rt


def sturb_w(rt, rn, imm):
    return 0x38000000 | ((imm & 0x1FF) << 12) | (rn << 5) | rt


def ldr_x_pre(rt, rn, off):                      # ldr xt,[xn,#off]!
    return 0xF8400C00 | ((off & 0x1FF) << 12) | (rn << 5) | rt


def ldrh_post(rt, rn, imm):                      # ldrh wt,[xn],#imm
    return 0x78400400 | ((imm & 0x1FF) << 12) | (rn << 5) | rt


def ldrb_post(rt, rn, imm):                      # ldrb wt,[xn],#imm
    return 0x38400400 | ((imm & 0x1FF) << 12) | (rn << 5) | rt


def ldr_x_lsl3(rt, rn, rm):                      # ldr xt,[xn,xm,lsl #3]
    return 0xF8607800 | (rm << 16) | (rn << 5) | rt


def ldr_w_lsl2(rt, rn, rm):                      # ldr wt,[xn,xm,lsl #2]
    return 0xB8607800 | (rm << 16) | (rn << 5) | rt


def str_w_reg(rt, rn, rm):                       # str wt,[xn,xm]
    return 0xB8206800 | (rm << 16) | (rn << 5) | rt


# ----- register pairs -------------------------------------------------------------------------------
def _pair(opcode, first, second, rn, off):
    assert off % 8 == 0 and -512 <= off < 512
    return opcode | (((off // 8) & 0x7F) << 15) | (second << 10) | (rn << 5) | first


def stp_off(t1, t2, rn, off):
    return _pair(0xA9000000, t1, t2, rn, off)


def stp_pre(t1, t2, rn, off):
    return _pair(0xA9800000, t1, t2, rn, off)


def ldp_off(t1, t2, rn, off):
    return _pair(0xA9400000, t1, t2, rn, off)


def ldp_post(t1, t2, rn, off):
    return _pair(0xA8C00000, t1, t2, rn, off)


# ----- moves and arithmetic -------------------------------------------------------------------------
def movz_w(rd, imm):
    return 0x52800000 | (imm << 5) | rd


def movk_w(rd, imm, shift):
    return 0x72800000 | ((shift // 16) << 21) | (imm << 5) | rd


def movz_x(rd, imm):
    return 0xD2800000 | (imm << 5) | rd


def mov_x(rd, rm):                               # orr xd,xzr,xm
    return 0xAA0003E0 | (rm << 16) | rd


def mov_w(rd, rm):                               # orr wd,wzr,wm
    return 0x2A0003E0 | (rm << 16) | rd


def addi_w(rd, rn, imm):                         # add wd,wn,#imm
    return 0x11000000 | (imm << 10) | (rn << 5) | rd




def add_xi(rd, rn, imm):                         # add xd,xn,#imm (rn may be sp)
    assert 0 <= imm < 4096
    return 0x91000000 | (imm << 10) | (rn << 5) | rd


def sub_wi(rd, rn, imm):
    return 0x51000000 | (imm << 10) | (rn << 5) | rd


def sub_xi(rd, rn, imm):
    return 0xD1000000 | (imm << 10) | (rn << 5) | rd


def subs_wi(rd, rn, imm):
    return 0x71000000 | (imm << 10) | (rn << 5) | rd


def add_w(rd, rn, rm):
    return 0x0B000000 | (rm << 16) | (rn << 5) | rd


def sub_w(rd, rn, rm):
    return 0x4B000000 | (rm << 16) | (rn << 5) | rd


def add_x_reg(rd, rn, rm):
    return 0x8B000000 | (rm << 16) | (rn << 5) | rd


def add_lsl2(rd, rn, rm):                        # add wd,wn,wm,lsl #2
    return 0x0B000800 | (rm << 16) | (rn << 5) | rd


def madd_w(rd, rn, rm, ra):                      # wd = wn * wm + wa
    return 0x1B000000 | (rm << 16) | (ra << 10) | (rn << 5) | rd


def cmp_w(rn, rm):
    return 0x6B00001F | (rm << 16) | (rn << 5)


def cmp_x(rn, rm):
    return 0xEB00001F | (rm << 16) | (rn << 5)


def cmp_i(rn, imm):
    return 0x71000000 | (imm << 10) | (rn << 5) | 31


def tst_w(rn, rm):
    return 0x6A000000 | (rm << 16) | (rn << 5) | 31


def orr_w(rd, rn, rm):
    return 0x2A000000 | (rm << 16) | (rn << 5) | rd


def eor_w(rd, rn, rm):
    return 0x4A000000 | (rm << 16) | (rn << 5) | rd


def and_w(rd, rn, rm):
    return 0x0A000000 | (rm << 16) | (rn << 5) | rd


def bic_w(rd, rn, rm):
    return 0x0A200000 | (rm << 16) | (rn << 5) | rd


_EOR_IMMEDIATE = {1: 0x52000000, 2: 0x521F0000, 3: 0x52000400}   # logical-immediate forms of 1, 2, 3


def eor_imm(rd, rn, value):                      # eor wd,wn,#value for value 1..3
    return _EOR_IMMEDIATE[value] | (rn << 5) | rd


def lslv_w(rd, rn, rm):
    return 0x1AC02000 | (rm << 16) | (rn << 5) | rd


def lsrv_w(rd, rn, rm):
    return 0x1AC02400 | (rm << 16) | (rn << 5) | rd


def lsli_w(rd, rn, shift):                       # lsl wd,wn,#shift (an alias of ubfm)
    return 0x53000000 | ((32 - shift) << 16) | ((31 - shift) << 10) | (rn << 5) | rd


def ubfx_w(rd, rn, lsb, width):
    return 0x53000000 | (lsb << 16) | ((lsb + width - 1) << 10) | (rn << 5) | rd


def bfi_w(rd, rn, lsb, width):
    return 0x33000000 | (((32 - lsb) % 32) << 16) | ((width - 1) << 10) | (rn << 5) | rd


def csel_w(rd, rn, rm, cond):
    return 0x1A800000 | (rm << 16) | (cond << 12) | (rn << 5) | rd


def cset_ls(rd):                                 # wd = 1 if LS (unsigned <=) else 0
    return 0x1A9F87E0 | rd


# ----- PC-relative addressing and branches ----------------------------------------------------------
def adr(rd, cur, target):
    distance = target - cur
    assert -(1 << 20) <= distance < (1 << 20)
    return 0x10000000 | ((distance & 3) << 29) | (((distance >> 2) & 0x7FFFF) << 5) | rd


def adrp_x(rd, cur, target):
    """adrp xd: the 4 KB page of `target`, relative to the page of `cur`."""
    pages = ((target & ~0xFFF) - (cur & ~0xFFF)) >> 12
    assert -(1 << 20) <= pages < (1 << 20), hex(pages << 12)
    return 0x90000000 | ((pages & 3) << 29) | (((pages >> 2) & 0x7FFFF) << 5) | rd


def b(cur, target):
    check_branch_range(cur, target, 26)
    return 0x14000000 | (((target - cur) >> 2) & 0x3FFFFFF)


def bl_(cur, target):
    check_branch_range(cur, target, 26)
    return 0x94000000 | (((target - cur) >> 2) & 0x3FFFFFF)


def blr_(rn):
    return 0xD63F0000 | (rn << 5)


def _compare_branch(opcode, rt, cur, target):
    check_branch_range(cur, target, 19)
    return opcode | ((((target - cur) >> 2) & 0x7FFFF) << 5) | rt


def cbz_x(rt, cur, target):
    return _compare_branch(0xB4000000, rt, cur, target)


def cbnz_x(rt, cur, target):
    return _compare_branch(0xB5000000, rt, cur, target)


def cbz_w(rt, cur, target):
    return _compare_branch(0x34000000, rt, cur, target)


def cbnz_w(rt, cur, target):
    return _compare_branch(0x35000000, rt, cur, target)


def bcond(cond, cur, target):
    check_branch_range(cur, target, 19)
    return 0x54000000 | ((((target - cur) >> 2) & 0x7FFFF) << 5) | cond


def tbz_w(rt, bit, cur, target):
    check_branch_range(cur, target, 14)
    return 0x36000000 | ((bit >> 5) << 31) | ((bit & 31) << 19) | ((((target - cur) >> 2) & 0x3FFF) << 5) | rt


def tbnz_w(rt, bit, cur, target):
    return tbz_w(rt, bit, cur, target) | 0x01000000


# ----- single-precision floating point --------------------------------------------------------------
def ldr_s(rt, rn, off):
    return 0xBD400000 | ((off // 4) << 10) | (rn << 5) | rt


def str_s(rt, rn, off):
    return 0xBD000000 | ((off // 4) << 10) | (rn << 5) | rt


def fadd_s(rd, rn, rm):
    return 0x1E202800 | (rm << 16) | (rn << 5) | rd


def fsub_s(rd, rn, rm):
    return 0x1E203800 | (rm << 16) | (rn << 5) | rd


def fmul_s(rd, rn, rm):
    return 0x1E200800 | (rm << 16) | (rn << 5) | rd


def fmov_s(rd, imm8):                            # fmov sd,#value, value encoded as imm8
    return 0x1E201000 | (imm8 << 13) | rd


FMOV_HALF, FMOV_FOUR = 0x60, 0x10                # the imm8 encodings of 0.5 and 4.0


# ----- the label assembler ----------------------------------------------------------------------------
class Asm:
    """Builds one code cave at `base`. Items are instruction words, or functions (pc, address_of) ->
    word for instructions that need a label's address. A branch target is a label name (str) or an
    absolute address (int); `address_of(None)` stays None, which callers use for "not known yet" on a
    first layout pass."""

    def __init__(self, base):
        self.base = base
        self.items = []
        self.labels = {}
        self._unique = 0

    # layout
    def label(self, name):
        assert name not in self.labels, ("label defined twice", name)
        self.labels[name] = len(self.items)

    def unique(self, prefix):
        self._unique += 1
        return '%s.%d' % (prefix, self._unique)

    def address_of(self, target):
        return self.base + 4 * self.labels[target] if isinstance(target, str) else target

    def pc(self):
        return self.base + 4 * len(self.items)

    def w(self, *words):
        self.items.extend(words)

    def _deferred(self, encode):
        self.items.append(encode)

    def words(self):
        return [item(self.base + 4 * index, self.address_of) if callable(item) else item
                for index, item in enumerate(self.items)]

    # branches and addresses by label
    def b(self, target):
        self._deferred(lambda pc, at: b(pc, at(target)))

    def bl(self, target):
        self._deferred(lambda pc, at: bl_(pc, at(target)))

    def bc(self, cond, target):
        self._deferred(lambda pc, at: bcond(cond, pc, at(target)))

    def cbz_x(self, rt, target):
        self._deferred(lambda pc, at: cbz_x(rt, pc, at(target)))

    def cbnz_x(self, rt, target):
        self._deferred(lambda pc, at: cbnz_x(rt, pc, at(target)))

    def cbz_w(self, rt, target):
        self._deferred(lambda pc, at: cbz_w(rt, pc, at(target)))

    def cbnz_w(self, rt, target):
        self._deferred(lambda pc, at: cbnz_w(rt, pc, at(target)))

    def tbz(self, rt, bit, target):
        self._deferred(lambda pc, at: tbz_w(rt, bit, pc, at(target)))

    def tbnz(self, rt, bit, target):
        self._deferred(lambda pc, at: tbnz_w(rt, bit, pc, at(target)))

    def adr(self, rd, target):
        self._deferred(lambda pc, at: adr(rd, pc, pc if at(target) is None else at(target)))

    def adrp(self, rd, page):
        self._deferred(lambda pc, at: adrp_x(rd, pc, page))

    def mov64(self, rd, value):
        """movz + three movk: a full 64-bit constant in xd."""
        self.w(movz_x(rd, value & 0xFFFF))
        for half in (1, 2, 3):
            self.w(0xF2800000 | (half << 21) | (((value >> (16 * half)) & 0xFFFF) << 5) | rd)
