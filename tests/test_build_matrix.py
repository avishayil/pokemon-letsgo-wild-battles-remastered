#!/usr/bin/env python3
"""Build-structure matrix over the generator's main flags, without game files.

Drives build_patch over profile x hp-gate x exp-recipients x catch action (off and ZL+RUN; the Y
catch builds are covered by test_xcatch.py) against a synthetic code image, under both encodings of
the no-recall cave's poke table (table and delta), and for ZL+RUN builds under both input sources.
For every combination it checks that the layout builds and passes the generator's own verify_caves
(capstone return branches, displaced instructions, stack balance), fits below the end of the code-cave page, keeps every scratch slot
inside the scratch page, and contains exactly the modules its flags select.

Run: python3 -I tests/test_build_matrix.py
"""
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import cave_end, check, fake_text, finish  # noqa: E402
import asm  # noqa: E402
import build_patch as bp  # noqa: E402

PROFILES = ('classic', 'timed')
HP_GATES = ('strict', 'slot6')
EXP_RECIPIENTS = ('party', 'participants')
CATCH_ACTIONS = ('off', 'zl-run')
NORECALL_ENCODINGS = ('table', 'delta')
QUICK_CATCH_MODULES = {'K', 'KM', 'G'}


def input_sources_for(catch_action):
    """The input source only changes Quick Catch (ZL+RUN) builds, so only those try both."""
    return ('handheld', 'all') if catch_action == 'zl-run' else ('handheld',)


def check_input_hook(tag, caves, input_source):
    """The all-styles input hooks the shared helper and reads the buttons from [x0+0x18]."""
    buttons_cave = caves['KM']
    want_hook = bp.HOOK_KS if input_source == 'all' else bp.HOOK_KM
    check(tag + ' KM hook', buttons_cave['hook'] == want_hook,
          'KM hook 0x%X != 0x%X' % (buttons_cave['hook'], want_hook))
    if input_source == 'all':
        check(tag + ' KM reads [x0+0x18]', asm.ldr_x(17, 0, 0x18) in buttons_cave['words'],
              'all-styles cave must load the buttons from [x0+0x18]')


def check_combo(text, tag, flags, input_source):
    """Build one flag combination and check its fit, scratch use and module membership."""
    profile, hp_gate, exp_recipients, catch_action = flags
    try:
        caves = bp.lay_out(text, profile, hp_gate, exp_recipients, catch_action, input_source=input_source)
        bp.verify_caves(text, caves)
    except Exception as error:  # noqa: BLE001 - surface any build/verify failure as a check
        check(tag, False, 'build/verify raised %r' % error)
        return
    if catch_action == 'zl-run':
        check_input_hook(tag, caves, input_source)

    end = cave_end(caves)
    check(tag + ' fit', end <= bp.CAVE_END_PAGE, 'ends %s' % hex(end))

    outside_page = sorted(hex(offset) for offset in bp.used_scratch(caves)
                          if not 0 <= offset < bp.SCRATCH_SIZE)
    check(tag + ' scratch', not outside_page, 'offsets outside page: %s' % outside_page)

    present = set(caves)
    has_battlers_only_exp = 'X' in present
    check(tag + ' X', has_battlers_only_exp == (exp_recipients == 'participants'),
          'X present=%s for recipients=%s' % (has_battlers_only_exp, exp_recipients))
    has_quick_catch = QUICK_CATCH_MODULES <= present
    check(tag + ' K/KM/G', has_quick_catch == (catch_action == 'zl-run'),
          'quick-catch modules present=%s for catch=%s' % (QUICK_CATCH_MODULES & present, catch_action))


def main():
    text = fake_text()
    flag_sets = list(itertools.product(PROFILES, HP_GATES, EXP_RECIPIENTS, CATCH_ACTIONS))
    combo_count = 0
    saved_encoding = bp.NORECALL_ENC
    try:
        for encoding, flags in itertools.product(NORECALL_ENCODINGS, flag_sets):
            bp.NORECALL_ENC = encoding
            for input_source in input_sources_for(flags[3]):
                tag = '%s/%s/%s/%s[N=%s,in=%s]' % (*flags, encoding, input_source)
                check_combo(text, tag, flags, input_source)
                combo_count += 1
    finally:
        bp.NORECALL_ENC = saved_encoding

    quick_catch_sets = sum(1 for flags in flag_sets if flags[3] == 'zl-run')
    finish('build matrix passed (%d combos: %d flag sets x %d N encodings, with the %d zl-run sets '
           'built for 2 input sources)'
           % (combo_count, len(flag_sets), len(NORECALL_ENCODINGS), quick_catch_sets))


if __name__ == '__main__':
    main()
