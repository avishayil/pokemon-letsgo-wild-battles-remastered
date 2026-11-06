#!/usr/bin/env python3
"""Structural tests of the generated code caves, without game files.

Builds every registry module against a synthetic code image (only the expected instruction at each
hook offset) and checks properties the generator's own control-flow checks do not cover: above all
the poke table embedded in the no-recall cave (both its plain and its delta encoding), the
displaced-instruction positions, the battlers-only EXP gate for each EXP scope, and the in-place
"no EXP from catching" patches.

Run: python3 -I tests/test_caves.py
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import (BUTTONS_MIRROR_SLOT, SCRATCH_PAGE_BIAS, WILD_ENCOUNTER_SLOT,  # noqa: E402
                      cave_end, check, fake_text, finish)
import asm  # noqa: E402
import build_patch as bp  # noqa: E402
from capstone import CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN, Cs  # noqa: E402

DISASSEMBLER = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
EE620_BSEQ_SIZE = 4908          # bytes in the trainer faint sequence file the poke table patches
EE620_POKE_COUNT = 38           # pokes the no-recall cave applies to that sequence
DELTA_NORECALL_WORDS = 41       # the delta-encoded no-recall cave: 8 words shorter than the table one
ADR_MASK, ADR_OPCODE = 0x9F000000, 0x10000000
CATCH_ACTIONS_WITH_CATCH_EXP = ('off', 'zl-run', 'x-lowhp')
DEFAULT_LAYOUT = ('classic', 'strict')     # profile, hp gate


def decode_adr(word, pc):
    """The address an AArch64 `adr` instruction at pc computes."""
    immhi = (word >> 5) & 0x7FFFF
    immlo = (word >> 29) & 0x3
    displacement = (immhi << 2) | immlo
    if displacement & (1 << 20):            # 21-bit signed immediate
        displacement -= 1 << 21
    return pc + displacement


def split_code_and_data(cave):
    """A cave's instruction words and its trailing data words (the registry declares the tail length)."""
    data_count = cave.get('data_words', 0)
    return cave['words'][:-data_count], cave['words'][-data_count:]


def test_option_combos_fit(text):
    """All four EXP-recipient x catch-action combos lay out, verify, and fit the cave page."""
    ends = {}
    for exp_recipients in ('party', 'participants'):
        for catch_action in ('off', 'zl-run'):
            caves = bp.lay_out(text, *DEFAULT_LAYOUT, exp_recipients, catch_action)
            bp.verify_caves(text, caves)
            end = cave_end(caves)
            ends[(exp_recipients, catch_action)] = end
            check('fit', end <= bp.CAVE_END_PAGE, '%s/%s ends %s' % (exp_recipients, catch_action, hex(end)))
    check('combo-superset', ends[('participants', 'zl-run')] >= ends[('party', 'off')])
    return len(ends)


def test_norecall_poke_table(caves):
    """The no-recall cave's table: sentinel-terminated u16 offsets equal to EE620_POKES, and an adr at it."""
    norecall = caves['N']
    check('N data_words', norecall.get('data_words', 0) > 0, 'registry must declare the table tail')
    code, data = split_code_and_data(norecall)
    halfwords = []
    for word in data:
        halfwords += [word & 0xFFFF, (word >> 16) & 0xFFFF]
    check('N sentinel', 0 in halfwords, 'no zero sentinel')
    sentinel_index = halfwords.index(0) if 0 in halfwords else len(halfwords)
    table = halfwords[:sentinel_index]
    check('N table == EE620_POKES', table == bp.EE620_POKES, '%d entries vs %d' % (len(table), len(bp.EE620_POKES)))
    check('N 38 entries', len(table) == EE620_POKE_COUNT, str(len(table)))
    check('N unique', len(set(table)) == len(table))
    check('N aligned', all(offset % 4 == 0 for offset in table))
    check('N bounds', all(0 < offset <= EE620_BSEQ_SIZE - 4 for offset in table), 'offset outside ee620.bseq')
    check('N tail-zeros', all(half == 0 for half in halfwords[sentinel_index:]), 'junk after sentinel')

    table_address = norecall['base'] + len(code) * 4      # the first data word
    adr_words = [(index, word) for index, word in enumerate(code) if (word & ADR_MASK) == ADR_OPCODE]
    check('N has adr', len(adr_words) == 1)
    if adr_words:
        index, word = adr_words[0]
        target = decode_adr(word, norecall['base'] + index * 4)
        check('N adr->table', target == table_address, 'adr lands at %s want %s' % (hex(target), hex(table_address)))


def test_module_structure(text, caves):
    """Displaced-instruction positions and key instructions of the ZL+RUN and battlers-only EXP caves."""
    run_decide = caves['K']['words']
    check('K dispfirst', caves['K'].get('dispfirst') is True)
    check('K displaced@0', run_decide[0] == bp.PREIMAGE[bp.HOOK_K])
    # x16 is the bare scratch page here, so the slot needs the page bias
    consume_mirror = asm.str_x(31, 16, SCRATCH_PAGE_BIAS + BUTTONS_MIRROR_SLOT)
    check('K consume-on-read', consume_mirror in run_decide, 'mirror must be zeroed after the read (stale-style safety)')
    check('KM displaced@-2', caves['KM']['words'][-2] == bp.PREIMAGE[bp.HOOK_KM])
    check('G displaced@-2', caves['G']['words'][-2] == bp.PREIMAGE[bp.HOOK_G])
    check('G has ret', asm.RET in caves['G']['words'], 'the skip path must ret')
    check('X displaced@-2', caves['X']['words'][-2] == bp.PREIMAGE[bp.HOOK_X])
    default_caves = bp.lay_out(text, *DEFAULT_LAYOUT, 'party', 'off')
    check('default has no K/KM/G/X', not ({'K', 'KM', 'G', 'X'} & set(default_caves)))


def test_battle_exp_scope(text):
    """The battlers-only EXP cave: scope 'wild' reads the wild-encounter flag, scope 'all' drops that gate."""
    wild_gate = asm.ldrb_w(17, 16, SCRATCH_PAGE_BIAS + WILD_ENCOUNTER_SLOT)   # ldrb w17,[x16,#0xFF8]
    wild_scope = bp.lay_out(text, *DEFAULT_LAYOUT, 'participants', 'off', exp_scope='wild')['X']
    all_scope = bp.lay_out(text, *DEFAULT_LAYOUT, 'participants', 'off', exp_scope='all')['X']
    check('X-wild has +0xF8 gate', wild_gate in wild_scope['words'], 'wild scope must read the wild-encounter flag')
    check('X-all drops +0xF8 gate', wild_gate not in all_scope['words'], 'all scope must NOT gate on the wild-encounter flag')
    # the gate is one load + one conditional branch
    check('X-all is shorter', len(all_scope['words']) == len(wild_scope['words']) - 2,
          'all=%d wild=%d' % (len(all_scope['words']), len(wild_scope['words'])))
    for scope, cave in (('wild', wild_scope), ('all', all_scope)):
        check('X-%s zeroes share' % scope, asm.movz_w(8, 0) in cave['words'], 'must force the share byte to 0')
        check('X-%s displaced@-2' % scope, cave['words'][-2] == bp.PREIMAGE[bp.HOOK_X])
        bp.verify_caves(text, {'X': cave})


def test_no_catch_exp_patches(text):
    """Catch EXP off = two in-place `return 0` patches (XF formula, XG gate) that cost no cave space."""
    return_zero = [asm.movz_w(0, 0), asm.RET]
    for catch_action in CATCH_ACTIONS_WITH_CATCH_EXP:
        layout = (text, *DEFAULT_LAYOUT, 'participants', catch_action)
        with_catch_exp = bp.lay_out(*layout, exp_scope='all', catch_exp='on')
        without_catch_exp = bp.lay_out(*layout, exp_scope='all', catch_exp='off')
        check('catch_exp on has no XF/XG (%s)' % catch_action, not ({'XF', 'XG'} & set(with_catch_exp)))
        check('catch_exp off has XF+XG (%s)' % catch_action, {'XF', 'XG'} <= set(without_catch_exp))
        for key, hook in (('XF', bp.HOOK_XF), ('XG', bp.HOOK_XG)):
            cave = without_catch_exp[key]
            check('%s in-place at hook (%s)' % (key, catch_action), cave.get('inplace') and cave['base'] == hook)
            check('%s is return-0 (%s)' % (key, catch_action), cave['words'] == return_zero, str(cave['words']))
            check('%s both words pinned' % key, hook in bp.PREIMAGE and hook + 4 in bp.PREIMAGE)
        unmoved = all(without_catch_exp[key]['base'] == with_catch_exp[key]['base'] for key in with_catch_exp)
        check('XF/XG cost 0 pad (%s)' % catch_action, unmoved, 'a cave moved')
        records = bp.variant_records('main', without_catch_exp)
        records_at_hooks = sum(1 for address, _ in records if address in (bp.HOOK_XF, bp.HOOK_XG))
        check('no redirect at XF/XG (%s)' % catch_action, records_at_hooks == 2, 'expect exactly the 2 bodies')
        bp.verify_caves(text, {key: without_catch_exp[key] for key in ('XF', 'XG')})


def decode_delta_table(data_words):
    """Delta table: a u16 first offset, then one byte per entry = (gap / 4), ended by a zero byte."""
    raw = b''.join(struct.pack('<I', word) for word in data_words)
    current = struct.unpack_from('<H', raw, 0)[0]
    decoded = [current]
    for gap_in_words in raw[2:]:
        if gap_in_words == 0:
            break
        current += gap_in_words * 4
        decoded.append(current)
    return decoded


def disassemble_set(words, base):
    code = b''.join(struct.pack('<I', word) for word in words)
    return {insn.mnemonic + ' ' + insn.op_str for insn in DISASSEMBLER.disasm(code, base)}


def test_norecall_delta_encoding(text):
    """Delta encoding: decodes to exactly EE620_POKES, scales gaps by lsl #2, and saves 8 words."""
    saved_encoding = bp.NORECALL_ENC
    try:
        bp.NORECALL_ENC = 'delta'
        caves = bp.lay_out(text, *DEFAULT_LAYOUT, 'participants', 'zl-run')
        bp.verify_caves(text, caves)
        norecall = caves['N']
        code, data = split_code_and_data(norecall)
        decoded = decode_delta_table(data)
        check('N-delta == EE620_POKES', decoded == bp.EE620_POKES, '%d decoded' % len(decoded))
        instructions = disassemble_set(code, norecall['base'])
        scales_by_four = any(instruction.startswith('add') and 'lsl #2' in instruction and 'lsl #30' not in instruction
                             for instruction in instructions)
        check('N-delta lsl#2', scales_by_four, 'delta decoder must scale by lsl #2')
        check('N-delta no bad shift', not any('lsl #30' in instruction for instruction in instructions), 'wrong shift amount')
        check('N-delta saves 8 words', len(norecall['words']) == DELTA_NORECALL_WORDS, str(len(norecall['words'])))
        check('N-delta fits', cave_end(caves) <= bp.CAVE_END_PAGE)
    finally:
        bp.NORECALL_ENC = saved_encoding


def main():
    text = fake_text()
    combo_count = test_option_combos_fit(text)
    caves = bp.lay_out(text, *DEFAULT_LAYOUT, 'participants', 'zl-run')
    test_norecall_poke_table(caves)
    test_module_structure(text, caves)
    test_battle_exp_scope(text)
    test_no_catch_exp_patches(text)
    test_norecall_delta_encoding(text)
    finish('cave structural tests passed (%d combos; N table verified)' % combo_count)


if __name__ == '__main__':
    main()
