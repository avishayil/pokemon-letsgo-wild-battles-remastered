#!/usr/bin/env python3
"""In-game Options rows, runtime toggles and the extras (trade evolution, caught marker, scripted fights
stay vanilla).

Saved state: bits 20..30 of the game's settings word (cfg+0x54, cfg = [[[[*0x15fad08]+0x98]+0x58]+0x98]),
one field per row. A stored field is "this value XOR this build's default", so a save that never saw the
mod (bits 0) starts at the defaults; the game's own reset (0x1c3440) clears the bits = back to defaults.
"""
import struct

import option_text                           # the rows' text in every language (tools/option_text.py)
from asm import (                            # the instruction encoders (tools/asm.py)
    Asm, EQ, FMOV_FOUR, sign_extend,
    FMOV_HALF, HI, HS, LDP_1617, LO, LS, NE, NOP, RET, STP_1617, add_w, addi_w, add_x_reg, add_xi,
    adrp_x, and_w, b, bfi_w, bic_w, bl_, cmp_i, cmp_w, cmp_x, eor_imm, eor_w, fadd_s, fmov_s, fmul_s, fsub_s,
    ldp_off, ldp_post, ldr_s, ldr_w, ldr_w_lsl2, ldr_x, ldr_x_lsl3, ldrb_w, ldrh_post, ldrh_w, ldur_w, lsli_w,
    lslv_w, lsrv_w, madd_w, mov_w, mov_x, movz_w, movz_x, orr_w, stp_off, stp_pre, str_s, str_w, strb_w,
    strh_w, stur_w, sturb_w, sub_w, sub_wi, sub_xi, subs_wi, ubfx_w,
)

bp = None   # build_patch module, bound by bind()


def bind(mod):
    global bp
    bp = mod


def scratch_offset(slot):
    """A scratch slot's offset from the scratch page (see build_patch.scratch_offset)."""
    return bp.scratch_offset(slot)


def game_addr(pikachu_address):
    """The same place in the game being built (identity for Pikachu; see build_patch.game_addr)."""
    return bp.game_addr(pikachu_address)


# ----- the rows ------------------------------------------------------------------------------------------
# Menu order. lsb/width = the field inside the 11-bit image (cfg bits 20..30); n = number of values.
# Value 0 is always "Off". Fixed per row so a save means the same in every build.
ROWS = [dict(key='wild', lsb=0, width=1, n=2), dict(key='quick', lsb=1, width=2, n=4),
        dict(key='share', lsb=3, width=2, n=3), dict(key='catch_exp', lsb=5, width=1, n=2),
        dict(key='timer', lsb=6, width=1, n=2), dict(key='marker', lsb=7, width=1, n=2),
        dict(key='recall', lsb=8, width=1, n=2), dict(key='trade', lsb=9, width=2, n=4)]
ROW = {r['key']: r for r in ROWS}
ROW_ORDER = [r['key'] for r in ROWS]
PAGE_ID = 11                         # the game's rows are 0..10; 11 = our page button on the game's page
FIRST_ID = 12                        # our rows (on our own page)
# Scratch slots (offsets on the shared scratch page; build_patch.verify_scratch_use checks that no
# other module writes them). 0x40..0x5F belong to the battle-result module, so these start at 0x60.
OPT_WORK = 0x60                      # u16: the Options screen's working image (effective values)
EV_OURS = 0x62                       # 1 = the running battle event was started by our routine
PAGE_FLAG = 0x63                     # 1 = the Options screen shows our page
CFG_SHIFT, CFG_WIDTH = 20, 11

# Pikachu addresses (mapped with game_addr())
# Code space: two functions of the game's protobuf text-format parser, which can never run (the game
# reaches it only for data it doesn't have). verify_pcave pins their original bytes, so a different build is refused.
PCAVE = (0x70E300, 0x712350)         # TextFormat ParserImpl
PCAVE2 = (0x6F6830, 0x6F7B10)        # io::Tokenizer
PCAVE_SHA256 = {                     # the bytes the dead-code proof covered (per game, mapped range)
    'pikachu': '7172fed7ebc8662035cafc19240ee3756e85f4cdbe63dff695967e0f71e1021b',
    'eevee': 'ff3b4e37d98421e620ef751481a6f75f9e322fd948234e68b63debf15eb73596',
}
PCAVE2_SHA256 = {
    'pikachu': '93911c52c919d14ecc51c73a7b994dee3db94cdf91c4775bb6bac04ee7361469',
    'eevee': 'af2e0c8c7610d8ab5ccda7962aa65ebc771ca4ef8026527026db928764bdc6e7',
}
SINGLETON_PAGE, SINGLETON_OFF = 0x15FA000, 0xD08     # the game singleton holder: [SINGLETON_PAGE + SINGLETON_OFF]
CFG_POINTER_CHAIN = (0, 0x98, 0x58, 0x98)            # from the holder to the config block (cfg)
CFG_WORD_OFFSET = 0x54                               # the settings word in cfg
LANG_PAGE, LANG_OFF = 0x1614000, 0xB34
ON_LABEL, OFF_LABEL = 'msg_option_05_01', 'msg_option_05_02'   # the game's own "On" / "Off"


def fnv1a64(s):
    h = 0xcbf29ce484222645
    for c in s.encode():
        h = ((h ^ c) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


def our_label(key, kind):           # our own label names (never present in the game's message files)
    return 'wbr_option_%s_%s' % (key, kind)


# ----- the assembler: tools/asm.py, plus two game-specific helpers --------------------------------------
class OptionsAsm(Asm):
    """asm.Asm with the scratch page and the saved settings word of the game. `opts` (from
    normalize) supplies each row's default for opt_field."""

    def __init__(self, base, opts=None):
        super().__init__(base)
        self.opts = opts

    def scratch_page(self, reg):
        """x`reg` = the scratch page; address a slot with scratch_offset(slot)."""
        self.adrp(reg, bp.SCRATCH_PAGE_ADDRESS)

    def game_rows_continue(self, text, id_reg, hook, original_label=None):
        """For the game's own rows (ID in w`id_reg` < PAGE_ID): the displaced instruction at `hook`, then
        back. Our IDs (the page button and our rows) continue at the label 'ours'."""
        self.w(cmp_i(id_reg, PAGE_ID))
        self.bc(HS, 'ours')
        if original_label:
            self.label(original_label)
        self.w(original_word(text, hook))
        self.b(hook + 4)
        self.label('ours')

    def opt_field(self, rd, key):
        """w`rd` = row `key`'s value (its cfg field XOR the build default; the default if the save is not
        reachable). Uses only `rd`; no flags; no memory writes. Values above the row's last are possible
        only from a damaged save; every user of a three-value field reads it so 3 acts like 2."""
        r = ROW[key]
        default = self.opts['default'][key]
        d, e = self.unique('optdef'), self.unique('optend')
        self.adrp(rd, SINGLETON_PAGE)
        self.w(ldr_x(rd, rd, SINGLETON_OFF))
        for off in CFG_POINTER_CHAIN:
            self.cbz_x(rd, d)
            self.w(ldr_x(rd, rd, off))
        self.cbz_x(rd, d)
        self.w(ldr_w(rd, rd, CFG_WORD_OFFSET), ubfx_w(rd, rd, CFG_SHIFT + r['lsb'], r['width']))
        if default:
            self.w(eor_imm(rd, rd, default))
        self.b(e)
        self.label(d)
        self.w(movz_w(rd, default))
        self.label(e)


# ----- liveness proof for gate sites --------------------------------------------------------------------
_MD = None
CALLER_SAVED = {'x%d' % i for i in range(18)} | {'nzcv'}


def _md():
    global _MD
    if _MD is None:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
        _MD = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
        _MD.detail = True
    return _MD


def _norm(n):
    if n in ('wzr', 'xzr', 'sp', 'wsp'):
        return None
    if n[0] == 'w' and n[1:].isdigit():
        return 'x' + n[1:]
    return n


def dead_at(text, addr, regs, depth=96):
    """True if none of `regs` (e.g. {'x8','nzcv'}) is read before written on any path from `addr`
    (inclusive). Calls kill caller-saved regs; an indirect branch or the depth limit counts as live."""
    disassembler = _md()
    callee_may_clobber = {'x%d' % i for i in range(9, 18)} | {'nzcv'}
    pending, seen = [(addr, frozenset(regs), 0)], set()
    while pending:
        pc, still_live, steps = pending.pop()
        if not still_live or (pc, still_live) in seen:
            continue
        seen.add((pc, still_live))
        if steps > depth:
            return False
        ins = next(disassembler.disasm(text[pc:pc + 4], pc), None)
        if ins is None:
            return False
        read_ids, written_ids = ins.regs_access()
        reads = {_norm(ins.reg_name(x)) for x in read_ids} - {None}
        writes = {_norm(ins.reg_name(x)) for x in written_ids} - {None}
        mnemonic = ins.mnemonic
        is_compare_branch = mnemonic in ('cbz', 'cbnz', 'tbz', 'tbnz')
        if is_compare_branch:
            reads.discard('nzcv')         # capstone lists NZCV as read; compare-and-branch never reads it
        if still_live & reads:
            return False
        still_live = still_live - writes
        if mnemonic in ('bl', 'blr'):
            still_live = still_live - CALLER_SAVED
        if mnemonic == 'ret':
            if still_live - callee_may_clobber:
                return False
            continue
        if mnemonic in ('br', 'braa', 'brab'):
            return False
        target = None
        if mnemonic == 'b' or mnemonic.startswith('b.') or is_compare_branch:
            target = int(ins.op_str.split('#')[-1], 16)
        if mnemonic == 'b':
            pending.append((target, still_live, steps + 1))
            continue
        if target is not None:
            pending.append((target, still_live, steps + 1))
        pending.append((pc + 4, still_live, steps + 1))
    return True


# ----- relocate a whole function --------------------------------------------------------------------------
def move_function(text, src, end, dst):
    """Copy text[src:end] to run at dst. Internal PC-relative refs keep their offsets; b/bl that leave
    the function and every adrp are re-encoded. Anything else PC-relative that leaves it is refused."""
    moved = []
    for old_pc in range(src, end, 4):
        word, = struct.unpack_from('<I', text, old_pc)
        new_pc = dst + (old_pc - src)
        if (word & 0x7C000000) == 0x14000000:                                # B / BL
            target = old_pc + sign_extend(word & 0x3FFFFFF, 26) * 4
            if not (src <= target < end):
                word = (word & 0xFC000000) | (((target - new_pc) >> 2) & 0x3FFFFFF)
        elif (word & 0x9F000000) == 0x90000000:                              # ADRP
            pages = sign_extend(((word >> 5) & 0x7FFFF) << 2 | ((word >> 29) & 3), 21)
            page = (old_pc & ~0xFFF) + (pages << 12)
            word = adrp_x(word & 31, new_pc, page)
        elif (word & 0xFF000010) == 0x54000000 or (word & 0x7E000000) in (0x34000000, 0x36000000):
            if (word & 0x7E000000) == 0x36000000:                            # TBZ / TBNZ
                target = old_pc + sign_extend((word >> 5) & 0x3FFF, 14) * 4
            else:                                                            # B.cond / CBZ / CBNZ
                target = old_pc + sign_extend((word >> 5) & 0x7FFFF, 19) * 4
            assert src <= target < end, ("move_function: conditional branch leaves the function", hex(old_pc))
        elif (word & 0x9F000000) == 0x10000000 or (word & 0x3B000000) == 0x18000000:
            raise AssertionError(("move_function: ADR / LDR-literal not supported", hex(old_pc), hex(word)))
        elif (word & 0xFFFFFC1F) == 0xD61F0000:                              # BR (jump table)
            raise AssertionError(("move_function: indirect branch (jump table?)", hex(old_pc)))
        moved.append(word)
    return moved


# ----- option spec --------------------------------------------------------------------------------------
def normalize(requested, catch_action, exp_recipients, catch_exp, profile='classic', exp_scope='all', recall=True):
    """Turn the requested options into the dict every builder here takes (`opts`).

    requested = dict(menu, page, defaults={row: value}, trade, scripted_vanilla, caught_marker). `trade` is
    0/False (not built), True, or 1..3 (built; its level when there is no menu).

    The result adds:
      rows           {row: default value} for the rows this build shows
      row_keys       those rows in menu order
      page           True if the rows sit on their own page (a button on the game's page opens it)
      present        {row: True if this build has the feature at all}
      default        {row: the value the build uses when the save has no setting}
      default_image  `default` packed into the 11-bit image (each row at its lsb)

    Rows exist only for features this build has; a feature without a row keeps its fixed build behaviour."""
    opts = dict(menu=False, defaults={}, trade=0, scripted_vanilla=False, caught_marker=False, page=True)
    opts.update(requested or {})
    trade = 3 if opts['trade'] is True else int(opts['trade'] or 0)
    opts['trade'] = trade
    present = dict(wild=True, quick=catch_action in ('x-lowhp', 'x-anytime'), share=exp_recipients == 'participants',
                   catch_exp=catch_exp == 'off', timer=profile == 'classic', marker=bool(opts['caught_marker']),
                   recall=bool(recall), trade=trade > 0)
    fixed = dict(wild=1, quick=3 if catch_action == 'x-anytime' else 1, share=1 if exp_scope == 'wild' else 0,
                 catch_exp=0, timer=0, marker=1, recall=0, trade=trade)
    wanted = dict(fixed, trade=0)            # with the menu, Trade Evolution starts Off unless told otherwise
    for key, value in opts['defaults'].items():
        assert key in ROW, ("unknown Options row", key)
        wanted[key] = int(value)
        assert 0 <= wanted[key] < ROW[key]['n'], ("Options row value out of range", key, value)
    rows = {key: wanted[key] for key in ROW_ORDER if opts['menu'] and present[key]}
    opts['rows'] = rows
    opts['row_keys'] = [key for key in ROW_ORDER if key in rows]
    opts['page'] = bool(opts['page'] and opts['row_keys'])
    opts['present'] = present
    opts['default'] = {key: rows.get(key, fixed[key]) for key in ROW_ORDER}
    opts['default_image'] = sum(opts['default'][key] << ROW[key]['lsb'] for key in ROW_ORDER)
    return opts


def active(opts):
    return bool(opts and (opts['row_keys'] or opts['trade'] or opts['scripted_vanilla'] or opts['caught_marker']))


# ----- the string / data block (OSTR) -------------------------------------------------------------------
ADDR = {}        # key -> cave base, filled by build_patch.lay_out's first pass
LAYOUT_TEXT = [None]   # the text.bin of the current layout (for original words in extra_records)


def _value_labels(opts):
    """row key -> [hash of the label for value 0..3] (game On/Off or ours)."""
    out = {}
    for k in opts['row_keys']:
        n = ROW[k]['n']
        hs = [fnv1a64(OFF_LABEL)]
        for v in range(1, 4):
            if v >= n:
                hs.append(hs[-1])
            elif n == 2 or option_text.VALUES.get(k, {}).get(v, 'x') is None:
                hs.append(fnv1a64(ON_LABEL))
            else:
                hs.append(fnv1a64(our_label(k, 'v%d' % v)))
        out[k] = hs
    return out


def ostr_layout(opts):
    """(words, offsets). HT/HD: u64 title/desc hash per row; VALS: 4 u64 value-label hashes per row;
    ROWT: 8 B per row (lsb, width, last value, mask); LBL: every label of ours (u64); OFFS: u32 per
    (label, language) from STR; STR: UTF-16LE, NUL-terminated."""
    keys = opts['row_keys']
    vl = _value_labels(opts)
    labels = []                                          # (hash, [10 strings])
    if opts['page']:
        labels.append((fnv1a64(our_label('page', 'title')), [title for title, _ in option_text.PAGE]))
        labels.append((fnv1a64(our_label('page', 'info')), [info for _, info in option_text.PAGE]))
        labels.append((fnv1a64(our_label('page', 'value')), list(option_text.PAGE_OPEN)))
    for k in keys:
        labels.append((fnv1a64(our_label(k, 'title')), [title for title, _ in option_text.TEXT[k]]))
        labels.append((fnv1a64(our_label(k, 'info')), [info for _, info in option_text.TEXT[k]]))
        for v, s in sorted(option_text.VALUES.get(k, {}).items()):
            if s is not None and v < ROW[k]['n']:
                labels.append((fnv1a64(our_label(k, 'v%d' % v)), s))
    q = lambda hs: b''.join(struct.pack('<Q', h) for h in hs)
    ht = q(fnv1a64(our_label(k, 'title')) for k in keys)
    hd = q(fnv1a64(our_label(k, 'info')) for k in keys)
    vals = q(h for k in keys for h in vl[k])
    rowt = b''.join(bytes([ROW[k]['lsb'], ROW[k]['width'], ROW[k]['n'] - 1, (1 << ROW[k]['width']) - 1, 0, 0, 0, 0])
                    for k in keys)
    lbl = q(h for h, _ in labels)
    blob, offs = bytearray(), []
    for _, strings in labels:
        for s in strings:
            offs.append(len(blob))
            blob += s.encode('utf-16-le') + b'\0\0'
    blob += b'\0' * (-len(blob) % 4)
    parts = [('HT', ht), ('HD', hd), ('VALS', vals), ('ROWT', rowt), ('LBL', lbl),
             ('OFFS', b''.join(struct.pack('<I', x) for x in offs)), ('STR', bytes(blob))]
    off, data = {}, b''
    for name, part in parts:
        off[name] = len(data)
        data += part
    off['NLBL'] = len(labels)
    return list(struct.unpack('<%dI' % (len(data) // 4), data)), off


def ostr_address(name, opts):          # None before the first layout pass knows OSTR (sizes don't depend on it)
    return ADDR['OSTR'] + ostr_layout(opts)[1][name] if 'OSTR' in ADDR else None


def build_ostr(opts):
    return lambda text, base: ostr_layout(opts)[0]


# ----- Options-screen modules ---------------------------------------------------------------------------
def original_word(text, a):
    return struct.unpack_from('<I', text, a)[0]


def build_ob1(opts):        # 0x4276c bl 0x45f00: room for our rows in the list vector
    n = len(opts['row_keys'])
    def f(text, base):
        cave = OptionsAsm(base, opts)
        if opts['page']:
            cave.scratch_page(10)
            cave.w(ldrb_w(10, 10, scratch_offset(PAGE_FLAG)))
            cave.cbnz_w(10, 'ours')
            cave.w(ldur_w(9, 29, -0x14), addi_w(9, 9, 1), stur_w(9, 29, -0x14))     # the game's page + our button
            cave.bl(game_addr(0x45F00))
            cave.w(ldur_w(9, 29, -0x14), sub_wi(9, 9, 1), stur_w(9, 29, -0x14))
            cave.b(game_addr(0x42770))
            cave.label('ours')                                                          # our page: only our rows
            cave.w(movz_w(9, n), stur_w(9, 29, -0x14))
            cave.bl(game_addr(0x45F00))
            cave.w(stur_w(31, 29, -0x14))
            cave.b(game_addr(0x42770))                          # the game's loop adds none
        else:
            cave.w(ldur_w(9, 29, -0x14), addi_w(9, 9, n), stur_w(9, 29, -0x14))
            cave.bl(game_addr(0x45F00))
            cave.w(ldur_w(9, 29, -0x14), sub_wi(9, 9, n), stur_w(9, 29, -0x14))
            cave.b(game_addr(0x42770))
        return cave.words()
    return f


def _push_id(cave, row_id):
    cave.w(ldr_x(0, 19, 0x278), add_xi(0, 0, 0xA0), sub_xi(1, 29, 0x28),
        movz_w(9, row_id), stur_w(9, 29, -0x28), sturb_w(31, 29, -0x24))
    cave.bl(bp.game_addr(0x46980))


def build_ob2(opts):        # 0x42804 (common exit): push our row IDs (or the page button)
    def f(text, base):
        cave = OptionsAsm(base, opts)
        if opts['page']:
            cave.scratch_page(10)
            cave.w(ldrb_w(10, 10, scratch_offset(PAGE_FLAG)))
            cave.cbnz_w(10, 'ours')
            _push_id(cave, PAGE_ID)
            cave.b('done')
            cave.label('ours')
        for i, _ in enumerate(opts['row_keys']):
            _push_id(cave, FIRST_ID + i)
        cave.label('done')
        cave.w(original_word(text, game_addr(0x42804)))                     # ldp x29,x30,[sp,#0x60]
        cave.b(game_addr(0x42808))
        return cave.words()
    return f


def _row_index(cave, dst, idreg, n, bad):
    cave.w(sub_wi(dst, idreg, FIRST_ID), cmp_i(dst, n))
    cave.bc(HS, bad)


def _rowt(cave, opts, r, base_reg, lsb, nmax, mask):
    """lsb/nmax/mask of row index w`r` from ROWT (base_reg is clobbered)."""
    cave.adr(base_reg, ostr_address('ROWT', opts))
    cave.w(lsli_w(r, r, 3), add_x_reg(base_reg, base_reg, r))
    cave.w(ldrb_w(lsb, base_reg, 0), ldrb_w(nmax, base_reg, 2), ldrb_w(mask, base_reg, 3))


def _value(cave, dst, work_reg, lsb, nmax, mask):
    """w`dst` = min(field, last value) of the working image."""
    cave.scratch_page(work_reg)
    cave.w(ldrh_w(work_reg, work_reg, scratch_offset(OPT_WORK)), lsrv_w(dst, work_reg, lsb),
                             and_w(dst, dst, mask), cmp_w(dst, nmax))
    c = cave.unique('v')
    cave.bc(LS, c)
    cave.w(mov_w(dst, nmax))
    cave.label(c)


def build_otx(opts):        # 0x44dd4 cmp w24,#0xa: title + value for our IDs
    n = len(opts['row_keys'])
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.game_rows_continue(text, 24, game_addr(0x44DD4))
        if opts['page']:
            cave.w(cmp_i(24, PAGE_ID))
            cave.bc(NE, 'row')
            cave.mov64(1, fnv1a64(our_label('page', 'title')))
            cave.w(mov_x(0, 19), mov_x(2, 21))
            cave.bl(game_addr(0x44130))                                                 # title -> text_00
            cave.mov64(1, fnv1a64(our_label('page', 'value')))
            cave.b(game_addr(0x45164))  # empty value -> text_01
            cave.label('row')
        _row_index(cave, 9, 24, n, 'out')
        cave.adr(10, ostr_address('HT', opts))
        cave.w(ldr_x_lsl3(1, 10, 9))
        cave.w(mov_x(0, 19), mov_x(2, 21))
        cave.bl(game_addr(0x44130))             # title -> text_00
        cave.w(sub_wi(9, 24, FIRST_ID), mov_w(15, 9))
        _rowt(cave, opts, 9, 10, 11, 12, 13)
        _value(cave, 14, 10, 11, 12, 13)
        cave.w(lsli_w(15, 15, 2), add_x_reg(15, 15, 14))
        cave.adr(10, ostr_address('VALS', opts))
        cave.w(ldr_x_lsl3(1, 10, 15))
        cave.b(game_addr(0x45164))                                                      # value -> text_01
        cave.label('out')
        cave.b(game_addr(0x45170))
        return cave.words()
    return f


def build_otg(opts):        # 0x3f8d4 sub w8,w22,#2: step our value by the press (w20 = -1 / +1), wrapping
    n = len(opts['row_keys'])
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.game_rows_continue(text, 22, game_addr(0x3F8D4))
        if opts['page']:
            cave.w(cmp_i(22, PAGE_ID))
            cave.bc(NE, 'row')                       # our page row: open our page
            cave.w(mov_x(0, 19))
            cave.bl(game_addr(0x3F7B0))                            # as the game's buttons do
            cave.w(mov_x(0, 19))
            cave.bl(game_addr(0x3E970))
            cave.w(ldr_x(8, 0, 0x90), movz_w(9, PAGE_CODE), str_w(9, 8, 0x10),   # ws+0x10 = 4
                movz_w(9, 1), str_w(9, 19, 0xB8))                       # finish the screen
            cave.b(game_addr(0x3FACC))
            cave.label('row')
        _row_index(cave, 9, 22, n, 'out')
        _rowt(cave, opts, 9, 10, 11, 12, 13)
        _value(cave, 15, 14, 11, 12, 13)                                        # w14 = image, w15 = value
        cave.w(add_w(15, 15, 20))                                               # + the press (signed)
        cave.tbnz(15, 31, 'tomax')
        cave.w(cmp_w(15, 12))
        cave.bc(LS, 'store')
        cave.w(movz_w(15, 0))
        cave.b('store')
        cave.label('tomax')
        cave.w(mov_w(15, 12))
        cave.label('store')
        cave.w(lslv_w(13, 13, 11), bic_w(14, 14, 13), lslv_w(15, 15, 11), orr_w(14, 14, 15))
        cave.scratch_page(10)
        cave.w(strh_w(14, 10, scratch_offset(OPT_WORK)))
        cave.b(game_addr(0x3FAB4))
        cave.label('out')
        cave.b(game_addr(0x3FACC))
        return cave.words()
    return f


def build_oar(opts):        # 0x452d0 (entry) sub w8,w1,#2: arrows (w8 = value, w9 = last value)
    n = len(opts['row_keys'])
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.game_rows_continue(text, 1, game_addr(0x452D0))
        _row_index(cave, 8, 1, n, 'out')
        _rowt(cave, opts, 8, 9, 10, 11, 12)
        _value(cave, 8, 13, 10, 11, 12)
        cave.w(mov_w(9, 11))
        cave.b(game_addr(0x45368))
        cave.label('out')
        cave.w(RET)
        return cave.words()
    return f


def build_ods(opts):        # 0x3ebc0 ldr x10,[x10,w20,sxtw #3]: our description label
    n = len(opts['row_keys'])
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.game_rows_continue(text, 20, game_addr(0x3EBC0), original_label='orig')
        if opts['page']:
            cave.w(cmp_i(20, PAGE_ID))
            cave.bc(NE, 'row')
            cave.mov64(9, fnv1a64(our_label('page', 'info')))
            cave.b(game_addr(0x3EBFC))
            cave.label('row')
        _row_index(cave, 9, 20, n, 'orig')
        cave.adr(11, ostr_address('HD', opts))
        cave.w(ldr_x_lsl3(9, 11, 9))
        cave.b(game_addr(0x3EBFC))
        return cave.words()
    return f


def _sanitize(cave, opts, reg, tmp):
    """A three-value field holding 3 (a damaged save) becomes its default."""
    for k in opts['row_keys']:
        r = ROW[k]
        if r['n'] == 3:
            ok = cave.unique('san')
            cave.w(ubfx_w(tmp, reg, r['lsb'], r['width']), cmp_i(tmp, 3))
            cave.bc(NE, ok)
            cave.w(movz_w(tmp, 3 << r['lsb']), bic_w(reg, reg, tmp),
                movz_w(tmp, opts['default'][k] << r['lsb']), orr_w(reg, reg, tmp))
            cave.label(ok)


def build_old(opts):        # 0x3b37c (load epilogue): cfg bits -> working image
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.w(ldr_x(9, 8, 8))
        cave.cbz_x(9, 'skip')
        cave.w(ldr_w(9, 9, CFG_WORD_OFFSET), ubfx_w(9, 9, CFG_SHIFT, CFG_WIDTH), movz_w(10, opts['default_image']),
            eor_w(9, 9, 10))
        _sanitize(cave, opts, 9, 10)
        cave.scratch_page(10)
        cave.w(strh_w(9, 10, scratch_offset(OPT_WORK)))
        cave.label('skip')
        cave.w(original_word(text, game_addr(0x3B37C)))
        cave.b(game_addr(0x3B380))
        return cave.words()
    return f


def build_oap(opts):        # 0x3b404 ldr x0,[x8,#8] (apply): working image -> cfg bits
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.w(ldr_x(9, 8, 8))
        cave.cbz_x(9, 'skip')
        cave.scratch_page(10)
        cave.w(ldrh_w(10, 10, scratch_offset(OPT_WORK)), movz_w(11, opts['default_image']),
                           eor_w(10, 10, 11), ldr_w(11, 9, CFG_WORD_OFFSET), bfi_w(11, 10, CFG_SHIFT, CFG_WIDTH),
                           str_w(11, 9, CFG_WORD_OFFSET))
        cave.label('skip')
        cave.w(original_word(text, game_addr(0x3B404)))
        cave.b(game_addr(0x3B408))
        return cave.words()
    return f


def build_och(opts):        # 0x3f494 ldr x0,[x19,#8] ("anything changed?"): count our rows
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.w(ldr_x(9, 19, 8))
        cave.cbz_x(9, 'orig')
        cave.w(ldr_w(9, 9, CFG_WORD_OFFSET), ubfx_w(9, 9, CFG_SHIFT, CFG_WIDTH), movz_w(10, opts['default_image']),
            eor_w(9, 9, 10))
        cave.scratch_page(10)
        cave.w(ldrh_w(10, 10, scratch_offset(OPT_WORK)), cmp_w(9, 10))
        cave.bc(NE, 'yes')
        cave.label('orig')
        cave.w(original_word(text, game_addr(0x3F494)))
        cave.b(game_addr(0x3F498))
        cave.label('yes')
        cave.b(game_addr(0x3F5F4))                                         # mov w0,#1; epilogue
        return cave.words()
    return f


def build_olk(opts):        # 0x67871c (after the prologue) mov x20,x0: our labels' strings
    languages = option_text.LANGS
    def f(text, base):
        cave = OptionsAsm(base, opts)
        nl = ostr_layout(opts)[1]['NLBL']
        cave.w(ldr_x(16, 1, 0))
        cave.adr(17, ostr_address('LBL', opts))
        cave.w(movz_w(9, 0))
        cave.label('loop')
        cave.w(ldr_x_lsl3(10, 17, 9), cmp_x(10, 16))
        cave.bc(EQ, 'found')
        cave.w(addi_w(9, 9, 1), cmp_i(9, nl))
        cave.bc(LO, 'loop')
        cave.w(original_word(text, game_addr(0x67871C)))
        cave.b(game_addr(0x678720))
        cave.label('found')
        cave.cbz_x(2, 'out')
        cave.adrp(10, LANG_PAGE)
        cave.w(ldr_w(10, 10, LANG_OFF), cmp_i(10, len(languages) - 1))
        cave.bc(LS, 'lang')
        cave.w(movz_w(10, option_text.LANGS.index('English')))              # unknown -> English
        cave.label('lang')
        cave.w(movz_w(11, len(languages)), madd_w(11, 9, 11, 10))
        cave.adr(12, ostr_address('OFFS', opts))
        cave.w(ldr_w_lsl2(13, 12, 11))
        cave.adr(14, ostr_address('STR', opts))
        cave.w(add_x_reg(20, 14, 13), mov_x(19, 2))
        cave.w(mov_x(0, 19))
        cave.bl(game_addr(0x3A860))
        cave.w(mov_x(0, 19))
        cave.bl(game_addr(0x3AA40))
        cave.label('ch')
        cave.w(ldrh_post(1, 20, 2))
        cave.cbz_w(1, 'done')
        cave.w(mov_x(0, 19))
        cave.bl(game_addr(0x3A9A0))
        cave.tbz(0, 0, 'full')
        cave.b('ch')
        cave.label('full')
        cave.w(mov_x(0, 19))
        cave.bl(game_addr(0x3AA40))
        cave.b('out')
        cave.label('done')
        cave.w(mov_x(0, 19))
        cave.bl(game_addr(0x3AA10))
        cave.label('out')
        cave.w(ldp_off(29, 30, 31, 0x10), ldp_post(20, 19, 31, 0x20), movz_w(0, 0), RET)
        return cave.words()
    return f


PAGE_CODE = 4            # the Options screen's result code for "open our page" (the game uses 2 GO, 3 controller)


def build_odi(opts):        # 0x3e930 mov w1,wzr: the screen's first description is row 0's; on our page, our first row's
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.w(movz_w(1, 0))
        cave.scratch_page(16)
        cave.w(ldrb_w(16, 16, scratch_offset(PAGE_FLAG)))
        cave.cbz_w(16, 'out')
        cave.w(movz_w(1, FIRST_ID))
        cave.label('out')
        cave.b(game_addr(0x3E934))
        return cave.words()
    return f


def build_opq(opts):        # 0x83bea0 ldr w8,[x19,#0x98] (the Options parent, state 1, after the screen closed)
    # The parent (0x83bdd0) recreates the Options screen in its state 0 (also its way back from GO /
    # Controller Settings). Code 4 = open our page; any code while our page is shown = back to the game's page.
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.w(original_word(text, game_addr(0x83BEA0)))
        cave.scratch_page(9)
        cave.w(cmp_i(8, PAGE_CODE))
        cave.bc(EQ, 'open')
        cave.w(ldrb_w(10, 9, scratch_offset(PAGE_FLAG)))
        cave.cbz_w(10, 'orig')
        cave.w(strb_w(31, 9, scratch_offset(PAGE_FLAG)))
        cave.b('again')              # leaving our page
        cave.label('open')
        cave.w(movz_w(10, 1), strb_w(10, 9, scratch_offset(PAGE_FLAG)))
        cave.label('again')
        cave.w(movz_w(8, 0))
        cave.b(game_addr(0x83BFA4))                 # state 0: create the screen again
        cave.label('orig')
        cave.b(game_addr(0x83BEA4))
        return cave.words()
    return f


# ----- A on the page button: the game's own button route ------------------------------------------------
# A row's button component reacts to A only when its flag +0x142 is set (input check 0x34b7f0); the row
# text setup sets it to "ID < 2" at 0x4497c, so only the game's two buttons take A. OBA sets it for our
# page button too. The press then runs the game's callback 0x44840, which (OBP) stores our ID and posts the
# decide event like a game button, and the decide handler (OBD) closes the screen with PAGE_CODE, the same
# close the left/right path makes. OPQ then opens our page.


def build_oba(opts):        # 0x4497c cmp w19,#2 (then cset w8,lo -> button +0x142): our page button takes A too
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.w(cmp_i(19, PAGE_ID))
        cave.bc(NE, 'game')
        cave.w(cmp_i(19, PAGE_ID + 1))          # PAGE_ID < PAGE_ID + 1: LO, so the cset gives 1
        cave.b(game_addr(0x44980))
        cave.label('game')
        cave.w(original_word(text, game_addr(0x4497C)))
        cave.b(game_addr(0x44980))
        return cave.words()
    return f


def build_obp(opts):        # 0x44854 cmp w8,#2 (A callback: game buttons are IDs 0/1): our page button posts decide too
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.w(cmp_i(8, PAGE_ID))
        cave.bc(EQ, 'button')
        cave.w(original_word(text, game_addr(0x44854)))
        cave.b(game_addr(0x44858))
        cave.label('button')
        cave.b(game_addr(0x4485C))              # store the ID at +0x288 and post event 1, as for IDs 0/1
        return cave.words()
    return f


def build_obd(opts):        # 0x3ef14 cmp w0,#1 (decide: the pressed button's ID): our page button closes with PAGE_CODE
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.w(cmp_i(0, PAGE_ID))
        cave.bc(EQ, 'page')
        cave.w(original_word(text, game_addr(0x3EF14)))
        cave.b(game_addr(0x3EF18))
        cave.label('page')                      # the game's own button close (as for code 2), with PAGE_CODE
        cave.w(mov_x(0, 19))
        cave.bl(game_addr(0x3F7B0))
        cave.w(mov_x(0, 19))
        cave.bl(game_addr(0x3E970))
        cave.w(ldr_x(8, 0, 0x90), movz_w(9, PAGE_CODE))
        cave.b(game_addr(0x3F084))              # ws+0x10 = w9, [screen+0xb8] = 1 (finish), return
        return cave.words()
    return f


# ----- gates --------------------------------------------------------------------------------------------
CATCH_APP = 0x2F6470      # the game's catch screen (what the routine call replaced)
FNB = (0x305970, 0x305EE0)


def build_ogw1(opts):       # 0x305934 bl -> here: the routine (On) or the vanilla catch app (Off)
    def f(text, base):
        cave = OptionsAsm(base, opts)
        if 'wild' in opts['rows']:
            cave.opt_field(16, 'wild')
            cave.cbz_w(16, 'off')
        if opts['scripted_vanilla']:
            cave.scratch_page(16)
            cave.w(movz_w(17, 1), strb_w(17, 16, scratch_offset(EV_OURS)))
        cave.b(bp.ROUTINE_ENTRY)
        if 'wild' in opts['rows']:
            cave.label('off')
            cave.b(game_addr(CATCH_APP))
        return cave.words()
    return f


def build_ogw2(opts):       # 0x3057B8 ldr x20,[x8,#..]: the base's field (On) or the original (Off)
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.opt_field(20, 'wild')
        cave.cbz_w(20, 'van')
        cave.w(ldr_x(20, 8, 0x68))
        cave.b(game_addr(0x3057BC))
        cave.label('van')
        cave.w(bp.BASE_PREIMAGE[game_addr(0x3057B8)])
        cave.b(game_addr(0x3057BC))
        return cave.words()
    return f


def build_ogb(opts):        # 0x2FDF34 / 0x2FDF80 bl -> here: fn B skipped (On) or the relocated fn B (Off)
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.opt_field(16, 'wild')
        cave.cbz_w(16, ADDR.get('OFNB', base))
        cave.w(RET)
        return cave.words()
    return f


def build_ofnb(opts):
    def f(text, base):
        return move_function(text, game_addr(FNB[0]), game_addr(FNB[1]), base)
    return f


def build_oevs(opts):       # 0x289B80 bl 0x28BC40 (the scripted fights' only entry): the event is not ours
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.scratch_page(16)
        cave.w(strb_w(31, 16, scratch_offset(EV_OURS)), strb_w(31, 16, scratch_offset(bp.SLOT_WILD_ENCOUNTER)))
        cave.b(game_addr(0x28BC40))
        return cave.words()
    return f


def build_oec(opts):        # 0x28CB10 mov w2,#0xa -> base count cave only for our event
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.scratch_page(2)
        cave.w(ldrb_w(2, 2, scratch_offset(EV_OURS)))
        cave.cbz_w(2, 'van')
        cave.b(bp.ROUTINE_COUNT_CAVE)
        cave.label('van')
        cave.w(bp.BASE_PREIMAGE[game_addr(0x28CB10)])
        cave.b(game_addr(0x28CB14))
        return cave.words()
    return f


def build_oee(opts):        # 0x28BE84 ldr w8,[x19,#0x68] -> base "state 9" only for our event
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.scratch_page(8)
        cave.w(ldrb_w(8, 8, scratch_offset(EV_OURS)))
        cave.cbz_w(8, 'van')
        cave.w(movz_w(8, 9))
        cave.b(game_addr(0x28BE94))
        cave.label('van')
        cave.w(bp.BASE_PREIMAGE[game_addr(0x28BE84)])
        cave.b(game_addr(0x28BE88))
        return cave.words()
    return f


OEF_TEMPS = (8, 9, 10, 11, 12, 13, 14, 15, 16, 17)


def build_oef(opts):        # 0x28BF1C cbz x20,... -> the routine's fallback only for our event
    def f(text, base):
        cave = OptionsAsm(base, opts)
        r = next((t for t in OEF_TEMPS if dead_at(text, game_addr(0x28BF1C), {'x%d' % t})), 8)   # proven in verify_options
        cave.scratch_page(r)
        cave.w(ldrb_w(r, r, scratch_offset(EV_OURS)))
        cave.cbz_w(r, 'van')
        cave.b(bp.routine_fallback())
        cave.label('van')
        cave.cbnz_x(20, 'go')
        cave.b(game_addr(0x28C1AC))
        cave.label('go')
        cave.b(game_addr(0x28BF20))
        return cave.words()
    return f


def build_oxg(opts):        # in front of cave_battlers_only_exp: Exp. Share On / Trainers (only outside the mod's wild battles) / Off
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.w(STP_1617)
        cave.opt_field(16, 'share')
        cave.cbz_w(16, 'x')                                                     # Off: battlers only
        cave.tbnz(16, 1, 'nat')                                                 # On (2; a damaged 3 too)
        cave.scratch_page(17)
        cave.w(ldrb_w(17, 17, scratch_offset(bp.SLOT_WILD_ENCOUNTER)))
        cave.cbnz_w(17, 'x')   # Trainers: wild = battlers
        cave.label('nat')
        cave.w(LDP_1617, original_word(text, bp.HOOK_X))
        cave.b(bp.HOOK_X + 4)
        cave.label('x')
        cave.w(LDP_1617)
        cave.b(ADDR.get('X', base))
        return cave.words()
    return f


def build_ong(opts):        # in front of cave_no_recall: Return to Ball On = the game's recall animation
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.w(STP_1617)
        cave.opt_field(16, 'recall')
        cave.cbnz_w(16, 'nat')
        cave.w(LDP_1617)
        cave.b(ADDR.get('N', base))
        cave.label('nat')
        cave.w(LDP_1617, original_word(text, bp.HOOK_N))
        cave.b(bp.HOOK_N + 4)
        return cave.words()
    return f


def build_otm(opts):        # cave_faint_and_timer's timer clear: Battle Timer On = keep the game's 5-minute limit
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.opt_field(10, 'timer')
        cave.cbnz_w(10, 'keep')
        cave.w(strh_w(31, 19, 0x562))
        cave.label('keep')
        cave.b(ADDR.get('A', base) + 4 * (bp.TIMER_WORD_INDEX + 1))        # back to the word after it
        return cave.words()
    return f


def build_ocx(opts, hook):  # catch-EXP function entry: On = native, Off = return 0 (XF/XG)
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.opt_field(16, 'catch_exp')
        cave.cbnz_w(16, 'nat')
        cave.w(movz_w(0, 0), RET)
        cave.label('nat')
        cave.w(original_word(text, hook))
        cave.b(hook + 4)
        return cave.words()
    return f


def build_oqg(opts):        # id-5 jump-table target: the Y catch row, Off / 25% / 50% / any HP (checked at the press)
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.opt_field(16, 'quick')
        cave.cbz_w(16, 'orig')
        cave.scratch_page(17)
        cave.w(ldrb_w(9, 17, scratch_offset(bp.SLOT_WILD_ENCOUNTER)))
        cave.cbz_w(9, 'orig')             # the mod's wild battle?
        cave.w(ldr_x(17, 17, scratch_offset(bp.SLOT_WILD_PARAM)))
        cave.cbz_x(17, 'orig')                 # its wild Pokémon
        cave.w(cmp_i(16, 3))
        cave.bc(EQ, 'catch')                                             # Any HP
        cave.w(movz_w(9, 3), sub_w(16, 9, 16))                                              # shift = 3 - value
        cave.w(ldrh_w(9, 17, 0x7C), ldrh_w(17, 17, 0x7E), lslv_w(17, 17, 16), cmp_w(17, 9))  # cur << shift vs max
        cave.bc(HI, 'orig')
        cave.label('catch')
        cave.scratch_page(17)
        cave.w(movz_w(9, 1), strb_w(9, 17, scratch_offset(bp.SLOT_CATCH_INTENT)), movz_w(8, bp.RUN_COMMAND_ID))
        cave.b(bp.CMD_COMMIT_TAIL)                                                             # RUN -> native capture
        cave.label('orig')
        cave.b(bp.ID5_ORIG_HANDLER)
        return cave.words()
    return f


EVO_TABLE, EVO_ENTRY5 = 0xF83810, 0xF83820


def build_te(opts):         # trade method (5) in the level-up check: pass at the row's level (30/40/50)
    def f(text, base):
        cave = OptionsAsm(base, opts)
        if 'trade' in opts['rows']:
            cave.opt_field(16, 'trade')
            cave.cbz_w(16, 'fail')
        cave.w(mov_x(0, 19))
        cave.bl(game_addr(0x726580))
        if 'trade' in opts['rows']:
            cave.opt_field(16, 'trade')
            cave.w(movz_w(17, 10), madd_w(16, 16, 17, 31), addi_w(16, 16, 20))                # 20 + 10 * value
        else:
            cave.w(movz_w(16, 20 + 10 * opts['trade']))
        cave.w(cmp_w(0, 16))
        cave.bc(LO, 'fail')
        cave.b(game_addr(0x722F50))
        cave.label('fail')
        cave.b(game_addr(0x722BC8))
        return cave.words()
    return f


# ----- registry -----------------------------------------------------------------------------------------
def modules(opts, keys_present):
    """Module dicts for build_patch.lay_out (region 'pcave'). `keys_present` = the other modules' keys.
    redir: 'b' = a b at `hook` (variant_records), 'bl' = a bl at `hook`, None = reached another way
    (a base override, a rodata entry or another gate)."""
    pages = (SINGLETON_PAGE, LANG_PAGE)
    m = []
    def add(key, hook, build, redir='b', region='pcave', **kw):
        assert key in MODULE_PURPOSE, ("add the module to MODULE_PURPOSE", key)
        m.append(dict(key=key, hook=hook, build=build, region=region, freeform=True, opt=True, redir=redir,
                      adrp_ok=pages, variants={'addon', 'main'}, scratch_slots=kw.pop('scratch_slots', ()),
                      scratch=kw.pop('scratch', 'options layer'), **kw))
    work_slots = ((OPT_WORK, 2),)
    page_slots = ((PAGE_FLAG, 1),)
    event_slots = ((EV_OURS, 1),)
    if opts['row_keys']:
        add('OSTR', 0, build_ostr(opts), redir=None, data=True, scratch='none; option labels, tables and strings')
        add('OB1', game_addr(0x4276C), build_ob1(opts))
        add('OB2', game_addr(0x42804), build_ob2(opts))
        add('OTX', game_addr(0x44DD4), build_otx(opts), scratch_slots=work_slots)
        add('OTG', game_addr(0x3F8D4), build_otg(opts), scratch_slots=work_slots)
        add('OAR', game_addr(0x452D0), build_oar(opts), scratch_slots=work_slots)
        add('ODS', game_addr(0x3EBC0), build_ods(opts))
        add('OLD', game_addr(0x3B37C), build_old(opts), scratch_slots=work_slots)
        add('OAP', game_addr(0x3B404), build_oap(opts), scratch_slots=work_slots)
        add('OCH', game_addr(0x3F494), build_och(opts), scratch_slots=work_slots)
        add('OLK', game_addr(0x67871C), build_olk(opts), entry_depth=1, region='pcave2')
    if opts['page']:
        add('OPQ', game_addr(0x83BEA0), build_opq(opts), scratch_slots=page_slots, region='pcave2')
        add('ODI', game_addr(0x3E930), build_odi(opts), scratch_slots=page_slots, region='pcave2')
    rows = opts['rows']
    if 'wild' in rows or opts['scripted_vanilla']:
        add('OGW1', game_addr(0x305934), build_ogw1(opts), redir=None, scratch_slots=event_slots)
    if 'wild' in rows:
        add('OGW2', game_addr(0x3057B8), build_ogw2(opts), redir=None)
        add('OGB', game_addr(0x2FDF34), build_ogb(opts), redir=None)
        add('OFNB', game_addr(FNB[0]), build_ofnb(opts), redir=None, relocated=True, region='pcave2',
            scratch='none; a relocated copy of the game function the base disables (used when wild battles are switched off)')
    if opts['scripted_vanilla']:
        add('OEVS', game_addr(0x289B80), build_oevs(opts), redir='bl', scratch_slots=event_slots)
        add('OEC', game_addr(0x28CB10), build_oec(opts), redir=None)
        add('OEE', game_addr(0x28BE84), build_oee(opts), redir=None)
        add('OEF', game_addr(0x28BF1C), build_oef(opts), redir=None)
    if 'share' in rows and 'X' in keys_present:
        add('OXG', bp.HOOK_X, build_oxg(opts), redir=None)
    if 'recall' in rows and 'N' in keys_present:
        add('ONG', bp.HOOK_N, build_ong(opts), redir=None)
    if 'timer' in rows and 'A' in keys_present:
        add('OTM', bp.HOOK_A, build_otm(opts), redir=None)
    if 'catch_exp' in rows:
        if 'XF' in keys_present:
            add('OXFG', bp.HOOK_XF, build_ocx(opts, bp.HOOK_XF), redir=None)
        if 'XG' in keys_present:
            add('OXGG', bp.HOOK_XG, build_ocx(opts, bp.HOOK_XG), redir=None)
    if 'quick' in rows and 'WCA' in keys_present:
        add('OQG', bp.RODATA_ID5_ENTRY[0], build_oqg(opts), redir=None)
    if opts['trade']:
        add('TE', EVO_ENTRY5, build_te(opts), redir=None)
    if opts['caught_marker']:
        add('CMF', 0, build_cmf(opts), redir=None, region='pcave2', scratch='none; pane lookup by hash (caught marker)')
        add('CM', game_addr(CM_HOOK), build_cm(opts), data_words=8 * 4, region='pcave2',
            scratch='none; caught marker beside the wild HP box')
    if opts['page']:
        add('OBA', game_addr(0x4497C), build_oba(opts), region='pcave2', scratch='none; A on the page button')
        add('OBP', game_addr(0x44854), build_obp(opts), region='pcave2', scratch='none; A on the page button')
        add('OBD', game_addr(0x3EF14), build_obd(opts), region='pcave2', scratch='none; A on the page button')
    return m


# Every module of the settings layer, in record order (build_patch emits them in this order).
MODULE_PURPOSE = {
    'OSTR': "data: our labels' text, their hash tables and the value lists",
    'OB1': "Options list builder (0x4276c): room for our rows",
    'OB2': "Options list builder (0x42804): append our row IDs, or the page button",
    'OTX': "row text (0x44dd4): our rows' title and value",
    'OTG': "left/right (0x3f8d4): step our value; on the page button, open our page",
    'OAR': "row arrows (0x452d0): our first/last value",
    'ODS': "row description (0x3ebc0)",
    'OLD': "screen load (0x3b37c): saved bits -> the working image",
    'OAP': "apply (0x3b404): the working image -> saved bits",
    'OCH': "'anything changed?' (0x3f494): our rows count too",
    'OLK': "label lookup (0x67871c): the strings of our labels",
    'OPQ': "the Options parent (0x83bea0): open and close our page",
    'ODI': "the first description (0x3e930): our first row's on our page",
    'OGW1': "Wild Battles row (0x305934): the routine, or the game's catch screen",
    'OGW2': "Wild Battles row (0x3057b8): the base's field load, or the original",
    'OGB': "Wild Battles row (0x2fdf34/0x2fdf80): skip fn B, or run its relocated copy",
    'OFNB': "the relocated copy of fn B (runs when Wild Battles is Off)",
    'OEVS': "scripted fights (0x289b80): the starting event is not ours",
    'OEC': "scripted fights (0x28cb10): the base's count edit only for our events",
    'OEE': "scripted fights (0x28be84): the base's state edit only for our events",
    'OEF': "scripted fights (0x28bf1c): the base's fallback only for our events",
    'OXG': "Exp. Share row: in front of module X",
    'ONG': "Return to Ball row: in front of module N",
    'OTM': "Battle Timer row: module A's timer word",
    'OXFG': "Catch EXP row: in front of XF (the catch-EXP formula)",
    'OXGG': "Catch EXP row: in front of XG (the catch-EXP screen gate)",
    'OQG': "Quick Catch row: in front of WCA (the id-5 jump-table entry)",
    'TE': "Level-Up Trade Evolution (the trade method of the evolution check)",
    'CMF': "caught marker: find a layout pane by its name hash",
    'CM': "caught marker: show it beside the wild Pokemon's HP box",
    'OBA': "page button (0x4497c): it takes A, like the game's two buttons",
    'OBP': "page button (0x44854): A posts the decide event, like the game's buttons",
    'OBD': "page button (0x3ef14): decide closes the screen with PAGE_CODE (OPQ opens our page)",
}
OPT_KEYS = list(MODULE_PURPOSE)


def inplace_gate(gate_key):
    """XF/XG with the catch-EXP row: the entry branches to the gate, the 2nd word is the original."""
    def f(text, base):
        return [b(base, ADDR.get(gate_key, base)), original_word(text, base + 4)]
    return f


def timer_word(text, at):
    """cave_faint_and_timer's timer word with the Battle Timer row: a branch to OTM (which returns to A+15)."""
    return b(at, ADDR.get('OTM', at))


BASE_RECORD_GATES = (
    ('OGW1', 0x305934, 'bl'),
    ('OGW2', 0x3057B8, 'b'),
    ('OGB', 0x2FDF34, 'bl'),
    ('OGB', 0x2FDF80, 'bl'),
    ('OEC', 0x28CB10, 'b'),
    ('OEE', 0x28BE84, 'b'),        # + the two original words after it (the base record covered them)
    ('OEF', 0x28BF1C, 'b'),
)


def extra_records(caves):
    """(records, intentional overlap addresses): base-record overrides and rodata entries."""
    def packed(*words):
        return b''.join(struct.pack('<I', w) for w in words)
    records, intentional = [], set()
    for key, pikachu_site, kind in BASE_RECORD_GATES:
        if key not in caves:
            continue
        site = game_addr(pikachu_site)
        words = [(bl_ if kind == 'bl' else b)(site, caves[key]['base'])]
        if key == 'OEE':
            words += [original_word(LAYOUT_TEXT[0], site + 4), original_word(LAYOUT_TEXT[0], site + 8)]
        records.append((site, packed(*words)))
        intentional.add(site)
    if 'TE' in caves:
        records.append((EVO_ENTRY5, struct.pack('<i', caves['TE']['base'] - EVO_TABLE)))
    return records, intentional


def verify_pcave(text, caves):
    """The reclaimed region holds exactly the analyzed (dead) parser code, and every options cave and
    data block lies inside it."""
    import hashlib
    for region, (a0, a1), pins in (('pcave', PCAVE, PCAVE_SHA256), ('pcave2', PCAVE2, PCAVE2_SHA256)):
        lo, hi = game_addr(a0), game_addr(a1 - 4) + 4
        got = hashlib.sha256(text[lo:hi]).hexdigest()
        assert got == pins[bp.GAME], ("reclaimed protobuf region differs from the proven bytes", region, bp.GAME, got)
        for k, c in caves.items():
            if c.get('region') == region:
                assert lo <= c['base'] and c['base'] + 4 * len(c['words']) <= hi, ("options cave outside its region", k)


def verify_options(text, caves):
    """Each cave's scratch registers (and flags) are dead where it re-enters the game's code."""
    t9_15 = {'x%d' % i for i in range(9, 16)}
    need = {   # key -> [(game address the cave branches to, registers it may have changed)]
        'OB1': [(game_addr(0x42770), {'x9', 'x10'})],
        'OB2': [(game_addr(0x42808), {'x9', 'x10'})],   # x0/x1: the last 0x46980 call's, as on the original path
        'OPQ': [(game_addr(0x83BEA4), {'x9', 'x10'}), (game_addr(0x83BFA4), {'x9', 'x10', 'nzcv'})],
        'ODI': [(game_addr(0x3E934), {'x16'})],
        'OBP': [(game_addr(0x4485C), {'nzcv'})],
        'OBD': [(game_addr(0x3F084), {'nzcv'})],
        'OTX': [(game_addr(0x45164), t9_15 | {'nzcv'}), (game_addr(0x45170), {'x9', 'nzcv'})],
        'OTG': [(game_addr(0x3F8D8), {'nzcv'}), (game_addr(0x3FAB4), t9_15 | {'nzcv'}), (game_addr(0x3FACC), {'x9', 'nzcv'})],
        'OAR': [(game_addr(0x452D4), {'nzcv'}), (game_addr(0x45368), {'x10', 'x11', 'x12', 'x13', 'nzcv'})],
        'ODS': [(game_addr(0x3EBC4), {'nzcv'}), (game_addr(0x3EBFC), {'x11', 'nzcv'})],
        'OLD': [(game_addr(0x3B380), {'x9', 'x10', 'nzcv'})],
        'OAP': [(game_addr(0x3B408), {'x9', 'x10', 'x11'})],
        'OCH': [(game_addr(0x3F498), {'x9', 'x10', 'nzcv'})],
        'OLK': [(game_addr(0x678720), {'x9', 'x10', 'x16', 'x17', 'nzcv'})],
        'OXG': [(bp.HOOK_X + 4, {'nzcv'})],
        'ONG': [(bp.HOOK_N + 4, {'nzcv'})],
        'OQG': [(bp.ID5_ORIG_HANDLER, {'x9', 'x16', 'x17', 'nzcv'}), (bp.CMD_COMMIT_TAIL, {'x9', 'x16', 'x17', 'nzcv'})],
        'TE': [(game_addr(0x722F50), {'x0', 'x16', 'x17', 'nzcv'}), (game_addr(0x722BC8), {'x0', 'x16', 'x17', 'nzcv'})],
        'OEC': [(game_addr(0x28CB14), set())],
        'OGW2': [(game_addr(0x3057BC), set())],
    }
    for k, sites in need.items():
        if k not in caves:
            continue
        for a, regs in sites:
            assert dead_at(text, a, regs), ("options cave %s: %s may be live at %#x" % (k, sorted(regs), a))
    if 'OEF' in caves:                       # its temp register (the first word's adrp) is dead at the site
        rt = caves['OEF']['words'][0] & 31
        assert dead_at(text, game_addr(0x28BF1C), {'x%d' % rt}), ("options cave OEF: x%d may be live" % rt)
    if 'OTM' in caves:                       # OTM runs inside cave_faint_and_timer: x10 is dead there (restored by LDP_910)
        timer_pc = caves['A']['base'] + 4 * bp.TIMER_WORD_INDEX
        assert caves['A']['words'][bp.TIMER_WORD_INDEX] == b(timer_pc, caves['OTM']['base']), (
            "cave_faint_and_timer does not branch to OTM")
    if 'OFNB' in caves:                                   # the copy decodes to the same program
        md = _md()
        lo, hi = game_addr(FNB[0]), game_addr(FNB[1])
        orig = list(md.disasm(text[lo:hi], lo))
        cp = b''.join(struct.pack('<I', w) for w in caves['OFNB']['words'])
        new = list(md.disasm(cp, caves['OFNB']['base']))
        assert len(orig) == len(new) == (hi - lo) // 4, "fn B copy does not decode 1:1"
        for i, j in zip(orig, new):
            assert i.mnemonic == j.mnemonic, ("fn B copy", hex(i.address), i.mnemonic, j.mnemonic)
            if i.op_str != j.op_str:
                ti, tj = int(i.op_str.split('#')[-1], 16), int(j.op_str.split('#')[-1], 16)
                inside = lo <= ti < hi
                want = caves['OFNB']['base'] + (ti - lo) if inside else ti
                if i.mnemonic == 'adrp':
                    want = ti
                assert tj == want, ("fn B copy target", hex(i.address), i.op_str, j.op_str)


def pane_hash(path):
    """The battle layout's pane key: FNV-1a 64 from seed 0xa65c2e0ef17ea79c over the '_'-joined path
    (reproduces all 197 panes of layout_setting_battle_top_00.bin)."""
    h = 0xa65c2e0ef17ea79c
    for c in path.encode():
        h = ((h ^ c) * 0x100000001b3) & 0xFFFFFFFFFFFFFFFF
    return h


# Layout pane fields: translate X/Y, scale X/Y, size W/H (floats), flags (bit 0 visible, bit 4 dirty), alpha
TX, TY, SX, SY, PW, PH, FLAGS, ALPHA = 0x30, 0x34, 0x48, 0x4C, 0x50, 0x54, 0x58, 0x59

CM_HOOK = 0xAC2540         # b 0xaf8530: the HUD update's tail call for the opponent's ball row
ROW_UPDATE, IS_WILD, BPP_OF, SPECIES_OF, IS_CAUGHT, SET_BALL = 0xAF8530, 0xAC0790, 0xABE6F0, 0x7247C0, 0x7E5D60, 0xAF83A0


def build_cmf(opts):          # leaf: x20 = layout, x0 = pane hash -> x0 = pane or 0 (x9..x13 only)
    def f(text, base):
        cave = OptionsAsm(base, opts)
        cave.w(ldr_x(9, 20, 0x220), ldr_x(10, 20, 0x228), mov_x(11, 0), movz_x(0, 0))
        cave.label('loop')
        cave.cbz_x(10, 'done')
        cave.w(ldr_x(12, 9, 0), ldr_x(13, 9, 8), add_xi(9, 9, 16), sub_xi(10, 10, 1), cmp_x(12, 11))
        cave.bc(NE, 'loop')
        cave.w(mov_x(0, 13))
        cave.label('done')
        cave.w(RET)
        return cave.words()
    return f


# build_cm's register roles; the letters are the ones its position formula uses.
REG_MARKER = 21       # R: the ball row (L_ballPlate_01), which becomes the marker
REG_HP_BOX = 22       # B: the opponent's HP box (L_pokePlate_03 or _02)
REG_HP_BOX_POS = 23   #    its battle position (1 or 3)
REG_HP_ANCHOR = 25    # A: the box's anchor (N_pos_RT_00)
REG_ROW_PARENT = 26   # C: the ball row's parent (N_pos_RT_01); before that, the visibility-table pointer
REG_HP_BG = 27        # G: the box background (plateBG_01)
REG_BALL_ICON = 28    # I: ball 0 (P_ballIcon_00); before that, each visibility entry's value


def build_cm(opts):
    vis = [('L_ballPlate_01_P_ballIcon_%02d' % i, int(i == 0)) for i in range(6)] + \
          [('L_ballPlate_01_P_ballBG_00', 0), ('L_ballPlate_01_P_ballBG_01', 0)]
    def f(text, base):
        cave = OptionsAsm(base, opts)
        def find(pane_path):                                     # x0 = the pane (or 0)
            cave.mov64(0, pane_hash(pane_path))
            cave.bl(ADDR.get('CMF', base))
        cave.w(stp_pre(29, 30, 31, -0x60), add_xi(29, 31, 0), stp_off(19, 20, 31, 0x10),
            stp_off(21, 22, 31, 0x20), stp_off(23, 24, 31, 0x30), stp_off(25, 26, 31, 0x40),
            stp_off(27, 28, 31, 0x50), mov_x(19, 0))
        cave.bl(game_addr(ROW_UPDATE))                                       # the game's own row update first
        if 'marker' in opts['rows']:
            cave.opt_field(8, 'marker')
            cave.cbz_w(8, 'out')
        cave.w(ldr_w(8, 19, 0x68))
        cave.cbz_w(8, 'out')             # own side: not ours
        cave.w(ldr_x(0, 19, 0x60))
        cave.bl(game_addr(IS_WILD))
        cave.w(cmp_i(0, 1))
        cave.bc(NE, 'out')
        cave.w(ldr_x(20, 19, 0x58))
        cave.cbz_x(20, 'out')
        find('L_ballPlate_01')
        cave.cbz_x(0, 'out')
        cave.w(mov_x(REG_MARKER, 0))
        for n, (plate, pos) in enumerate((('L_pokePlate_03', 1), ('L_pokePlate_02', 3))):
            nxt = 'hide' if n else 'p2'
            if n:
                cave.label('p2')
            find(plate)
            cave.cbz_x(0, nxt)
            cave.w(ldrb_w(8, 0, FLAGS))
            cave.tbz(8, 0, nxt)
            cave.w(mov_x(REG_HP_BOX, 0), movz_w(REG_HP_BOX_POS, pos))
            find('N_pos_RT_00')
            cave.cbz_x(0, 'hide')
            cave.w(mov_x(REG_HP_ANCHOR, 0))
            find(plate + '_P_plateBG_01')
            cave.cbz_x(0, 'hide')
            cave.w(mov_x(REG_HP_BG, 0))
            cave.b('have')
        cave.label('have')
        cave.w(ldrb_w(8, REG_HP_ANCHOR, ALPHA), cmp_i(8, 0x80))
        cave.bc(LO, 'hide')             # the box is fading out
        cave.w(ldr_x(0, 19, 0x60), mov_w(1, REG_HP_BOX_POS))
        cave.bl(game_addr(BPP_OF))
        cave.cbz_x(0, 'hide')
        cave.w(ldr_x(0, 0, 0x60))
        cave.cbz_x(0, 'hide')
        cave.bl(game_addr(SPECIES_OF))
        cave.cbz_w(0, 'hide')
        cave.bl(game_addr(IS_CAUGHT))
        cave.tbz(0, 0, 'hide')
        cave.w(ldrb_w(8, REG_MARKER, FLAGS))
        cave.tbnz(8, 0, 'shown')                             # first show:
        cave.w(mov_x(0, 19), movz_w(1, 0), movz_w(2, 0))
        cave.bl(game_addr(SET_BALL))      # ball 0 = Poké Ball
        cave.label('shown')
        cave.adr(REG_ROW_PARENT, 'vis')                         # (the table pointer, for now)
        cave.w(movz_w(24, len(vis)))
        cave.label('vl')
        cave.w(ldr_x(0, REG_ROW_PARENT, 0), ldr_x(REG_BALL_ICON, REG_ROW_PARENT, 8), add_xi(REG_ROW_PARENT, REG_ROW_PARENT, 16))
        cave.bl(ADDR.get('CMF', base))
        cave.cbz_x(0, 'vn')
        cave.w(ldrb_w(8, 0, FLAGS), movz_w(9, 1), bic_w(8, 8, 9), orr_w(8, 8, REG_BALL_ICON), strb_w(8, 0, FLAGS))
        cave.label('vn')
        cave.w(subs_wi(24, 24, 1))
        cave.bc(NE, 'vl')
        find('N_pos_RT_01')
        cave.cbz_x(0, 'hide')
        cave.w(mov_x(REG_ROW_PARENT, 0))
        find('L_ballPlate_01_P_ballIcon_00')
        cave.cbz_x(0, 'hide')
        cave.w(mov_x(REG_BALL_ICON, 0))
        # Letters as in the REG_* table:
        # R.TX = A.TX - C.TX + B.TX + B.SX*(G.TX - .5*G.SX*G.W) - 4 - R.SX*(I.TX + .5*I.SX*I.W)
        cave.w(fmov_s(7, FMOV_HALF),
            ldr_s(0, REG_HP_BG, TX), ldr_s(1, REG_HP_BG, SX), ldr_s(2, REG_HP_BG, PW), fmul_s(1, 1, 2), fmul_s(1, 1, 7), fsub_s(0, 0, 1),
            ldr_s(1, REG_HP_ANCHOR, TX), ldr_s(2, REG_ROW_PARENT, TX), fsub_s(1, 1, 2), ldr_s(2, REG_HP_BOX, TX), fadd_s(1, 1, 2),
            ldr_s(2, REG_HP_BOX, SX), fmul_s(2, 2, 0), fadd_s(1, 1, 2), fmov_s(3, FMOV_FOUR), fsub_s(1, 1, 3),
            ldr_s(2, REG_BALL_ICON, SX), ldr_s(3, REG_BALL_ICON, PW), fmul_s(2, 2, 3), fmul_s(2, 2, 7), ldr_s(3, REG_BALL_ICON, TX), fadd_s(2, 3, 2),
            ldr_s(3, REG_MARKER, SX), fmul_s(2, 3, 2), fsub_s(1, 1, 2), str_s(1, REG_MARKER, TX))
        # R.TY = A.TY - C.TY + B.TY + B.SY*(G.TY - .5*G.SY*G.H) - R.SY*I.TY
        cave.w(ldr_s(0, REG_HP_BG, TY), ldr_s(1, REG_HP_BG, SY), ldr_s(2, REG_HP_BG, PH), fmul_s(1, 1, 2), fmul_s(1, 1, 7), fsub_s(0, 0, 1),
            ldr_s(1, REG_HP_ANCHOR, TY), ldr_s(2, REG_ROW_PARENT, TY), fsub_s(1, 1, 2), ldr_s(2, REG_HP_BOX, TY), fadd_s(1, 1, 2),
            ldr_s(2, REG_HP_BOX, SY), fmul_s(2, 2, 0), fadd_s(1, 1, 2),
            ldr_s(2, REG_MARKER, SY), ldr_s(3, REG_BALL_ICON, TY), fmul_s(2, 2, 3), fsub_s(1, 1, 2), str_s(1, REG_MARKER, TY))
        cave.w(ldrb_w(8, REG_MARKER, FLAGS), movz_w(9, 0x11), orr_w(8, 8, 9), strb_w(8, REG_MARKER, FLAGS))   # visible + dirty
        cave.b('out')
        cave.label('hide')
        cave.w(ldrb_w(8, REG_MARKER, FLAGS), movz_w(9, 1), bic_w(8, 8, 9), strb_w(8, REG_MARKER, FLAGS))
        cave.label('out')
        cave.w(ldp_off(19, 20, 31, 0x10), ldp_off(21, 22, 31, 0x20), ldp_off(23, 24, 31, 0x30),
            ldp_off(25, 26, 31, 0x40), ldp_off(27, 28, 31, 0x50), ldp_post(29, 30, 31, 0x60), RET)
        while (base + 4 * len(cave.items)) % 8:
            cave.w(NOP)
        cave.label('vis')
        for h, v in vis:
            x = pane_hash(h)
            cave.w(x & 0xFFFFFFFF, x >> 32, v, 0)
        return cave.words()
    return f
