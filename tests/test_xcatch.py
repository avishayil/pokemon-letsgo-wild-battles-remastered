#!/usr/bin/env python3
"""Structural tests of X-CATCH (catch a weakened wild Pokémon from the battle menu), without game
files, against a synthetic code image.

The feature is two caves in the code-cave page. The eligibility cave (WCH, at the per-button menu
update) records which battler is the wild Pokémon. The action cave (WCA) is the target of the
command-menu jump-table entry for button id 5: in a wild battle it reads that Pokémon's live HP when
the button is pressed and, if eligible, commits the RUN action through the shared commit tail with
the catch-intent flag set, so the after-battle cave turns the run into a capture; otherwise it falls
through to the game's own id-5 handler. These tests check both caves verify, each build stage fits
the cave page, the id-5 jump-table record points at WCA and nothing else is patched, and X-CATCH and
Quick Catch never share modules.

Run: python3 -I tests/test_xcatch.py
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import CATCH_INTENT_SLOT, X_UNLOCK_SLOT, check, fake_text, finish  # noqa: E402
import asm  # noqa: E402
import build_patch as bp  # noqa: E402

RUN_ACTION = 4               # the command-menu action id of RUN
POKEPARAM_MAX_HP = 0x7C      # u16 fields of a battler (BTL_POKEPARAM)
POKEPARAM_CURRENT_HP = 0x7E
QUICK_CATCH_MODULES = {'K', 'KM', 'G'}

# In these caves x16 = scratch page + bias, so the slot offsets are used directly.
STORE_CATCH_INTENT = asm.strb_w(17, 16, CATCH_INTENT_SLOT)
STORE_UNLOCK = asm.strb_w(17, 16, X_UNLOCK_SLOT)
LOAD_UNLOCK = asm.ldrb_w(17, 16, X_UNLOCK_SLOT)


def xcatch_keys(caves):
    return {key for key in caves if key.startswith('WC')}


def test_caves_verify(text):
    """WCH is a normal trampoline; WCA is freeform (a jump-table target that tail-branches into the game)."""
    caves = {}
    base = bp.CAVE_BASE
    eligibility = bp.cave_xcatch_eligibility(text, base, False)
    caves['WCH'] = dict(hook=bp.HOOK_WCH, base=base, words=eligibility)
    base += len(eligibility) * 4
    action = bp.cave_xcatch_action(text, base)
    caves['WCA'] = dict(hook=bp.RODATA_ID5_ENTRY[0], base=base, freeform=True, no_redir=True, words=action)
    try:
        bp.verify_caves(text, caves)
    except Exception as error:  # noqa: BLE001 - report any verify failure as a check
        check('WC caves verify_caves', False, repr(error))
    check('WCA commits RUN action 4', asm.movz_w(8, RUN_ACTION) in action,
          'WCA must set w8=4 (RUN -> result 3 -> cave_after_battle capture)')
    check('WCA arms catch_intent', STORE_CATCH_INTENT in action, 'WCA must set catch_intent +0xDC')


def lay_out_stage(text, stage, norecall_encoding):
    """Lay out an x-lowhp build the way lay_out does (x-* builds force the delta encoding in main()).
    Returns (first free cave byte, caves)."""
    saved_encoding = bp.NORECALL_ENC
    bp.NORECALL_ENC = norecall_encoding
    try:
        caves = {}
        cursor = bp.CAVE_BASE
        modules = bp.module_table('classic', 'strict', 'participants', 'x-lowhp',
                                  input_source='handheld', x_stage=stage)
        for module in modules:
            check('%s: %s in the .text padding' % (stage, module['key']), module.get('region') is None,
                  'the Y-catch modules live in the end-of-.text padding')
            if module.get('inplace'):        # rewrites its hook site in place: no cave space
                continue
            words = module['build'](text, cursor)
            caves[module['key']] = dict(module, base=cursor, words=words)
            cursor += len(words) * 4
        return cursor, caves
    finally:
        bp.NORECALL_ENC = saved_encoding


def test_stage_fit(observe_end, observe_caves, divert_end, divert_caves):
    check('observe fits', observe_end <= bp.CAVE_END_PAGE, 'ends 0x%X' % observe_end)
    check('observe modules', xcatch_keys(observe_caves) == {'WCH'},
          'want just WCH, got %s' % xcatch_keys(observe_caves))
    check('divert fits (delta N)', divert_end <= bp.CAVE_END_PAGE, 'ends 0x%X > pad' % divert_end)
    check('divert modules', xcatch_keys(divert_caves) == {'WCH', 'WCA'},
          'want WCH+WCA, got %s' % xcatch_keys(divert_caves))


def test_press_time_eligibility(observe_caves, divert_caves):
    """The product (x-lowhp divert): WCH only records the wild battler pointer and WCA reads its live HP
    at the press. A latch set while the menu was built went stale after an attack (the first press
    opened the game's own view), so the latch must not come back."""
    eligibility = divert_caves['WCH']['words']
    action = divert_caves['WCA']['words']
    check('WCH stores wild ptr', asm.str_x(21, 16, bp.SLOT_WILD_PARAM) in eligibility, 'no str x21 -> wild-ptr slot')
    check('WCH no unlock latch', STORE_UNLOCK not in eligibility, 'WCH still latches unlock')
    reads_live_hp = (asm.ldrh_w(9, 17, POKEPARAM_MAX_HP) in action
                     and asm.ldrh_w(17, 17, POKEPARAM_CURRENT_HP) in action)
    check('WCA reads live HP', reads_live_hp, 'WCA must read max/cur HP at the press')
    check('WCA no unlock read', LOAD_UNLOCK not in action, 'WCA still reads the latch')
    check('observe keeps latch', STORE_UNLOCK in observe_caves['WCH']['words'], 'observe WCH lost unlock')


def test_id5_routing(observe_caves, divert_caves):
    """The divert build re-points the id-5 jump-table entry at WCA and patches nothing else."""
    extras = dict(bp.extra_records(divert_caves))
    entry_address, table_base = bp.RODATA_ID5_ENTRY
    entry = extras.get(entry_address)
    # a jump-table entry is a signed 32-bit offset from the table base
    points_at_action = entry is not None and table_base + struct.unpack('<i', entry)[0] == divert_caves['WCA']['base']
    check('id-5 entry -> WCA', points_at_action, 'id-5 entry missing or wrong target')
    stale = {bp.HOOK_WCV, bp.RODATA_CLS_ENTRY[0], bp.WC_NOP1} & set(extras)
    check('no WCV/classifier/NOP records', not stale, 'stale extra records leaked: %s' % sorted(hex(address) for address in stale))
    check('observe has no routing record', not bp.extra_records(observe_caves),
          'observe must emit no id-5 routing')


def test_mode_exclusivity(text, observe_caves):
    check('x-build no ZL', not (QUICK_CATCH_MODULES & set(observe_caves)), 'ZL modules leaked into an x build')
    quick_catch = bp.lay_out(text, 'classic', 'strict', 'participants', 'zl-run')
    check('zl-run no WC', not xcatch_keys(quick_catch), 'WC modules leaked into zl-run')


def main():
    text = fake_text()
    test_caves_verify(text)
    observe_end, observe_caves = lay_out_stage(text, 'observe', 'delta')
    divert_end, divert_caves = lay_out_stage(text, 'divert', 'delta')
    test_stage_fit(observe_end, observe_caves, divert_end, divert_caves)
    test_press_time_eligibility(observe_caves, divert_caves)
    test_id5_routing(observe_caves, divert_caves)
    test_mode_exclusivity(text, observe_caves)
    finish('X-CATCH id-5 design tests passed (WCH+WCA valid; observe/divert fit the pad; '
           'id-5 redirect correct; exclusivity)')


if __name__ == '__main__':
    main()
