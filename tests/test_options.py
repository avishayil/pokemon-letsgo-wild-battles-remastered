#!/usr/bin/env python3
"""Tests of the in-game Options layer (tools/build_options.py): its text, its option spec, its
instruction encoders, its scratch slots and the IPS32 writer; with game files, full builds for both games; with the
game's romfs, the game's own labels and the caught-marker pane hashes.

The game-file-free checks always run. With WB_TEXT_BIN (and WB_TEXT_BIN_EEVEE) every options build
runs through all the generator checks (build_patch.lay_out, verify_options, verify_pcave). With
WB_ROMFS (the romfs `bin/` folder of your own dump) the On/Off labels, our label names and the
caught-marker pane hashes are checked against the game's files.

Run: python3 -I tests/test_options.py
"""
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, checks_run, fake_text, finish  # noqa: E402
import asm  # noqa: E402
import build_options as bo  # noqa: E402
import build_patch as bp  # noqa: E402
import option_text as ot  # noqa: E402
from capstone import CS_ARCH_ARM64, CS_MODE_ARM, Cs  # noqa: E402

DISASSEMBLER = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
LANGUAGE_COUNT = 10

# Longest line of the game's own msg_option_*_info texts and longest title, per language
# (measured from option.dat on 2026-11-05): ours must be no longer.
DESC_MAX = dict(JPN=17, JPN_KANJI=16, English=47, French=48, Italian=51, German=48, Spanish=52, Korean=23,
                Simp_Chinese=14, Trad_Chinese=14)
TITLE_MAX = dict(JPN=15, JPN_KANJI=13, English=24, French=25, Italian=25, German=24, Spanish=27, Korean=14,
                 Simp_Chinese=13, Trad_Chinese=13)
# The game's own value labels, +1 for CJK (wide column).
VALUE_MAX = dict(JPN=5, JPN_KANJI=5, English=8, French=11, Italian=20, German=14, Spanish=11, Korean=5,
                 Simp_Chinese=4, Trad_Chinese=4)

# Where each row's value sits in the packed default image (pinned here, independent of build_options).
FIELD_LSB = dict(wild=0, quick=1, share=3, marker=7, trade=9)
GAME_CONFIG_BITS = 20        # the game uses config bits 0-19; ours start above them
CONFIG_WORD_BITS = 32
LABEL_KINDS = ('title', 'info', 'v1', 'v2', 'v3')
GAME_OPTION_LABEL_COUNT = 100

# The values the game itself uses for its On/Off labels (at 0x44fcc / 0x45114).
ON_LABEL_HASH = 0xac17de9a8330dbf2
OFF_LABEL_HASH = 0xac17dd9a8330da3f

# Caught-marker pane hashes, checked against the battle layout file.
PANE_HASHES = {
    'L_ballPlate_01': 0x28551600e02f94aa,
    'L_ballPlate_01_P_ballIcon_00': 0xc0e1fa8bcdf59a93,
    'N_pos_RT_00': 0x0c9ed29d0aca7dc7,
    'L_pokePlate_03': 0xc299d666cb84fcdc,
}
LAYOUT_PANES = ['L_ballPlate_01', 'N_pos_RT_00', 'N_pos_RT_01', 'L_pokePlate_03', 'L_pokePlate_02',
                'L_pokePlate_03_P_plateBG_01', 'L_pokePlate_02_P_plateBG_01', 'L_ballPlate_01_P_ballBG_00',
                'L_ballPlate_01_P_ballBG_01'] + ['L_ballPlate_01_P_ballIcon_%02d' % icon for icon in range(6)]

FULL_OPTIONS = dict(menu=True, defaults={}, trade=True, scripted_vanilla=True, caught_marker=True)
OPTION_BUILDS = [
    FULL_OPTIONS,
    dict(menu=True),
    dict(menu=True, page=True),
    dict(menu=False, trade=True),
    dict(scripted_vanilla=True),
    dict(caught_marker=True),
    dict(menu=False, trade=1, caught_marker=True),
    dict(menu=True, defaults=dict(wild=0, share=1, quick=3, trade=2, timer=1, marker=0, recall=1), trade=True),
]
PRODUCT_LAYOUT = dict(x_stage='divert', exp_scope='all', catch_exp='off')
# Where the Options scratch slots used to sit (OPT_WORK u16, EV_OURS u8, PAGE_FLAG u8): inside the
# battle-event result cave's u64 at +0x48. The negative scratch test puts them back there.
OLD_OPT_WORK, OLD_EV_OURS, OLD_PAGE_FLAG = 0x48, 0x4A, 0x4B
IPS32_MAX_RECORD = 0xFFFF
IPS32_TEST_IMAGE_SIZE = 0x800000


def disassemble(word, address=0):
    instruction = next(DISASSEMBLER.disasm(struct.pack('<I', word), address), None)
    return (instruction.mnemonic + ' ' + instruction.op_str) if instruction else None


def default_image(**values):
    image = 0
    for key, value in values.items():
        image |= value << FIELD_LSB[key]
    return image


def is_two_lines_within(text, limit):
    lines = text.split('\n')
    return len(lines) == 2 and all(len(line) <= limit for line in lines)


def test_value_labels():
    for key, values in ot.VALUES.items():
        check('value labels for known values %s' % key, key in bo.ROW and set(values) <= set(range(1, bo.ROW[key]['n'])))
        for value, strings in values.items():
            if strings is None:
                continue
            fits = all(0 < len(label) <= VALUE_MAX[language] for label, language in zip(strings, ot.LANGS))
            check('value label length %s=%s' % (key, value), fits, str(strings))
    for key in bo.ROW_ORDER:
        value_count = bo.ROW[key]['n']
        if value_count > 2:
            has_labels = key in ot.VALUES and all(value in ot.VALUES[key] for value in range(1, value_count))
            check('value labels present %s' % key, has_labels)


def test_row_text():
    """Every row has a title and a two-line description in every language, within the game's lengths."""
    check('every row has text', set(ot.TEXT) == set(bo.ROW_ORDER), str(sorted(ot.TEXT)))
    for key, rows in ot.TEXT.items():
        check('%s has every language' % key, len(rows) == len(ot.LANGS) == LANGUAGE_COUNT)
        for language, (title, description) in zip(ot.LANGS, rows):
            label = '%s/%s' % (key, language)
            check('title length %s' % label, 0 < len(title) <= TITLE_MAX[language], title)
            lines = description.split('\n')
            check('description is two lines %s' % label, len(lines) == 2 and all(lines))
            check('description line length %s' % label, all(len(line) <= DESC_MAX[language] for line in lines),
                  description)
            check('no NUL %s' % label, '\0' not in title + description)
    for language, (title, description) in zip(ot.LANGS, ot.PAGE):
        fits = 0 < len(title) <= TITLE_MAX[language] and is_two_lines_within(description, DESC_MAX[language])
        check('page button text %s' % language, fits)


def our_label_hashes():
    return [bo.fnv1a64(bo.our_label(key, kind)) for key in bo.ROW_ORDER + ['page'] for kind in LABEL_KINDS]


def test_labels():
    """Our labels are distinct from each other and from the game's Options labels."""
    ours = our_label_hashes()
    check('label hash collision', len(set(ours)) == len(ours))
    game_names = ['msg_option_%02d%s' % (number, suffix) for number in range(GAME_OPTION_LABEL_COUNT)
                  for suffix in ('', '_info', '_00', '_01', '_02', '_03')]
    check('our label collides with a game label', not set(ours) & {bo.fnv1a64(name) for name in game_names})
    check('On/Off label hashes', bo.fnv1a64(bo.ON_LABEL) == ON_LABEL_HASH and bo.fnv1a64(bo.OFF_LABEL) == OFF_LABEL_HASH)


def test_spec():
    spec = bo.normalize(dict(menu=True, trade=True, caught_marker=True), 'x-lowhp', 'participants', 'off')
    check('row keys', spec['row_keys'] == ['wild', 'quick', 'share', 'catch_exp', 'timer', 'marker', 'recall', 'trade'],
          str(spec['row_keys']))
    check('defaults', spec['default'] == dict(wild=1, quick=1, share=0, catch_exp=0, timer=0, marker=1, recall=0, trade=0),
          str(spec['default']))
    check('default image', spec['default_image'] == default_image(wild=1, quick=1, marker=1), bin(spec['default_image']))

    spec = bo.normalize(dict(menu=True), 'off', 'party', 'on', profile='timed', recall=False)
    check('nothing else to toggle', spec['row_keys'] == ['wild'], str(spec['row_keys']))

    spec = bo.normalize(dict(menu=False, trade=True), 'x-lowhp', 'participants', 'off')
    check('no menu: a built feature is fixed (Lv. 50)', spec['row_keys'] == [] and spec['default']['trade'] == 3)

    spec = bo.normalize(dict(menu=False, trade=1), 'x-anytime', 'participants', 'off', exp_scope='wild')
    fixed = spec['default']['trade'] == 1 and spec['default']['quick'] == 3 and spec['default']['share'] == 1
    check('no menu: fixed values', fixed, str(spec['default']))

    builder_defaults = dict(trade=3, wild=0, share=2, quick=2)
    spec = bo.normalize(dict(menu=True, trade=True, defaults=builder_defaults), 'x-lowhp', 'participants', 'off')
    check('defaults from the builder', spec['default_image'] == default_image(quick=2, share=2, marker=1, trade=3),
          bin(spec['default_image']))
    check('no options = no settings layer', not bo.active(bo.normalize(None, 'x-lowhp', 'participants', 'off')))

    fields = sorted((row['lsb'], row['lsb'] + row['width']) for row in bo.ROWS)
    no_overlap = all(end <= next_start for (_, end), (next_start, _) in zip(fields, fields[1:]))
    check('fields overlap', no_overlap and fields[-1][1] <= bo.CFG_WIDTH)
    check('cfg bits above the game\'s', bo.CFG_SHIFT == GAME_CONFIG_BITS and bo.CFG_SHIFT + bo.CFG_WIDTH <= CONFIG_WORD_BITS)
    check('a value does not fit its field', all(row['n'] <= 1 << row['width'] for row in bo.ROWS))


# The instruction encoders the Options layer uses (tools/asm.py).
ENCODINGS = [
    (asm.ubfx_w(16, 16, 24, 1), 'ubfx w16, w16, #0x18, #1'),
    (asm.bfi_w(11, 10, 24, 5), 'bfi w11, w10, #0x18, #5'),
    (asm.ldur_w(9, 29, -0x14), 'ldur w9, [x29, #-0x14]'),
    (asm.stur_w(9, 29, -0x28), 'stur w9, [x29, #-0x28]'),
    (asm.sturb_w(31, 29, -0x24), 'sturb wzr, [x29, #-0x24]'),
    (asm.ldr_x_lsl3(1, 10, 9), 'ldr x1, [x10, x9, lsl #3]'),
    (asm.ldr_w_lsl2(13, 12, 11), 'ldr w13, [x12, x11, lsl #2]'),
    (asm.madd_w(11, 9, 11, 10), 'madd w11, w9, w11, w10'),
    (asm.lsrv_w(10, 10, 9), 'lsr w10, w10, w9'),
    (asm.lslv_w(12, 12, 9), 'lsl w12, w12, w9'),
    (asm.ldrh_post(1, 20, 2), 'ldrh w1, [x20], #2'),
    (asm.cmp_x(10, 16), 'cmp x10, x16'),
    (asm.fmov_s(7, asm.FMOV_HALF), 'fmov s7, #0.50000000'),
    (asm.fmov_s(3, asm.FMOV_FOUR), 'fmov s3, #4.00000000'),
    (asm.ldr_s(0, 27, 0x30), 'ldr s0, [x27, #0x30]'),
    (asm.str_s(1, 21, 0x34), 'str s1, [x21, #0x34]'),
    (asm.fmul_s(2, 2, 3), 'fmul s2, s2, s3'),
    (asm.fsub_s(1, 1, 2), 'fsub s1, s1, s2'),
    (asm.fadd_s(1, 1, 2), 'fadd s1, s1, s2'),
    (asm.add_w(15, 15, 20), 'add w15, w15, w20'),
    (asm.sub_w(16, 9, 16), 'sub w16, w9, w16'),
    (asm.and_w(8, 8, 12), 'and w8, w8, w12'),
    (asm.strh_w(14, 10, 0xF48), 'strh w14, [x10, #0xf48]'),
    (asm.ldrh_w(9, 17, 0x7C), 'ldrh w9, [x17, #0x7c]'),
] + [(asm.eor_imm(16, 16, value), 'eor w16, w16, #%d' % value) for value in (1, 2, 3)]

# (word, address it sits at, expected disassembly)
BRANCH_ENCODINGS = [
    (asm.tbz_w(10, 0, 0x100, 0x120), 0x100, 'tbz w10, #0, #0x120'),
    (asm.tbnz_w(8, 0, 0x100, 0xF0), 0x100, 'tbnz w8, #0, #0xf0'),
]


def test_encoders():
    for word, want in ENCODINGS:
        got = disassemble(word)
        check('encode %s' % want, got == want, '%s -> %s' % (hex(word), got))
    for word, address, want in BRANCH_ENCODINGS:
        got = disassemble(word, address)
        check('encode %s' % want, got == want, '%s -> %s' % (hex(word), got))


def test_pane_hashes():
    for name, want in PANE_HASHES.items():
        check('pane hash %s' % name, bo.pane_hash(name) == want, hex(bo.pane_hash(name)))


def parse_ips32(patch):
    """IPS32 bytes -> [(memory offset, data)]."""
    check('IPS32 header and footer', patch[:5] == b'IPS32' and patch[-4:] == b'EEOF')
    position = 5
    records = []
    while position < len(patch) - 4:
        offset, length = struct.unpack_from('>IH', patch, position)
        position += 6
        records.append((offset - bp.PCHTXT_OFF, patch[position:position + length]))
        position += length
    return records


def test_ips32(ips_path):
    records = [(0x305934, b'\x01\x02\x03\x04'),
               (0x70E300, bytes(range(256)) * 300),        # longer than one IPS32 record: must split
               (0x42804, b'\xaa' * 8)]
    bp.emit_ips32(records, ips_path)
    parsed = parse_ips32(ips_path.read_bytes())
    check('IPS32 keeps every byte', sum(len(data) for _, data in parsed) == sum(len(data) for _, data in records))
    lowest_start = min(start for start, _ in records)
    check('IPS32 sorted and split',
          all(len(data) <= IPS32_MAX_RECORD for _, data in parsed) and parsed[0][0] == lowest_start)
    rebuilt = bytearray(IPS32_TEST_IMAGE_SIZE)
    for start, data in parsed:
        rebuilt[start:start + len(data)] = data
    check('IPS32 round trip', all(bytes(rebuilt[start:start + len(data)]) == data for start, data in records))


def build_options_product(text, game, options, ips_path):
    """One options build through every generator check, plus its IPS32 copy."""
    caves = bp.lay_out(text, 'classic', 'strict', 'participants', 'x-lowhp', options=options, **PRODUCT_LAYOUT)
    bp.verify_caves(text, caves)
    bp.verify_cave_padding(text, caves)
    base, _ = bp.unified_base(text, 'self')
    merged = base + bp.variant_records('main', caves) + bp.extra_records(caves)
    bp.verify_marker(merged)
    bp.overlap_check('options', merged)
    merged = bp.disjoint(merged)
    bp.emit_ips32(merged, ips_path)
    round_trip = sorted(parse_ips32(ips_path.read_bytes()))
    check('%s IPS32 == pchtxt records' % game,
          b''.join(data for _, data in round_trip) == b''.join(data for _, data in sorted(merged)))
    pad_caves = [cave for cave in caves.values()
                 if cave.get('region') is None and not cave.get('inplace')]
    pad_end = max(cave['base'] + 4 * len(cave['words']) for cave in pad_caves)
    check('%s: the full pad is untouched' % game, pad_end <= bp.CAVE_END_PAGE, hex(pad_end))
    problem = scratch_use_problem(caves)
    check('%s: options build uses only registered scratch slots' % game, problem is None, problem or '')


def scratch_use_problem(caves):
    """None if build_patch.verify_scratch_use accepts the layout, else its complaint."""
    try:
        bp.verify_scratch_use(caves)
    except AssertionError as error:
        return repr(error)
    return None


def lay_out_options_dry_run(text, options):
    """An options layout on the synthetic image. strict=False skips the checks that read the game's own
    code (the reclaimed region's pinned bytes, the registers live at each return site), which a
    synthetic image cannot pass; the scratch-slot check below reads only the caves."""
    saved_encoding = bp.NORECALL_ENC
    bp.NORECALL_ENC = 'delta'               # as build_patch.main() does for x-* builds
    try:
        return bp.lay_out(text, 'classic', 'strict', 'participants', 'x-lowhp', options=options, strict=False,
                          **PRODUCT_LAYOUT)
    finally:
        bp.NORECALL_ENC = saved_encoding


def test_scratch_slots():
    """Every options build stores only into scratch slots one module owns. At their old offsets the
    Options slots overlapped the slots of the battle-event result cave (ST), which must be caught."""
    text = fake_text()
    for options in OPTION_BUILDS:
        caves = lay_out_options_dry_run(text, options)
        problem = scratch_use_problem(caves)
        check('registered scratch use %s' % sorted(options), problem is None, problem or '')

    caves = lay_out_options_dry_run(text, FULL_OPTIONS)
    st_writes = bp.scratch_writes(caves['ST'])
    check('ST writes its u64 at +0x48', (OLD_OPT_WORK, 8) in st_writes, str(sorted(st_writes)))

    saved_slots = (bo.OPT_WORK, bo.EV_OURS, bo.PAGE_FLAG)
    try:
        bo.OPT_WORK, bo.EV_OURS, bo.PAGE_FLAG = OLD_OPT_WORK, OLD_EV_OURS, OLD_PAGE_FLAG
        problem = scratch_use_problem(lay_out_options_dry_run(text, FULL_OPTIONS))
    finally:
        bo.OPT_WORK, bo.EV_OURS, bo.PAGE_FLAG = saved_slots
    check('old Options slots clash with ST', problem is not None and "'ST'" in problem,
          problem or 'verify_scratch_use accepted the colliding layout')


PAGE_BUTTON_MODULES = ('OBA', 'OBP', 'OBD')


def test_page_button():
    """A on the page button: its three hooks exist exactly when the rows have their own page, and the
    decide hook closes the screen with PAGE_CODE (the code the page hook OPQ opens our page on)."""
    text = fake_text()
    with_page = lay_out_options_dry_run(text, dict(menu=True, page=True))
    without_page = lay_out_options_dry_run(text, dict(menu=True, page=False))
    check('page button hooks with the page', all(key in with_page for key in PAGE_BUTTON_MODULES))
    check('no page button hooks without the page', not any(key in without_page for key in PAGE_BUTTON_MODULES))
    decide = with_page['OBD']['words']
    check('decide closes with PAGE_CODE', asm.movz_w(9, bo.PAGE_CODE) in decide)


def test_full_builds(ips_path):
    """Needs WB_TEXT_BIN / WB_TEXT_BIN_EEVEE; returns how many options builds ran."""
    built = 0
    games = (('pikachu', os.environ.get('WB_TEXT_BIN')), ('eevee', os.environ.get('WB_TEXT_BIN_EEVEE')))
    for game, path in games:
        if not path:
            continue
        text = Path(path).read_bytes()
        bp.set_game(game)
        saved_encoding = bp.NORECALL_ENC
        bp.NORECALL_ENC = 'delta'
        try:
            for options in OPTION_BUILDS:
                build_options_product(text, game, options, ips_path)
                built += 1
            caves = bp.lay_out(text, 'classic', 'strict', 'participants', 'x-lowhp', **PRODUCT_LAYOUT)
            check('%s: no options modules without options' % game, not any(key in caves for key in bo.OPT_KEYS))
        finally:
            bp.NORECALL_ENC = saved_encoding
            bp.set_game('pikachu')
    return built


def read_message_table(path):
    """A game message table: u32 count at +4, then (u64 label hash, u16 length, text) entries from +8."""
    table = path.read_bytes()
    count = struct.unpack_from('<I', table, 4)[0]
    position = 8
    labels = {}
    for _ in range(count):
        label_hash, length = struct.unpack_from('<QH', table, position)
        position += 10
        labels[label_hash] = table[position:position + length].rstrip(b'\0').decode()
        position += length
    return labels


def test_game_files(romfs):
    """Needs WB_ROMFS: the game's On/Off labels exist, ours do not, and our pane hashes are in its layout."""
    ours = set(our_label_hashes())
    for language in ot.LANGS:
        labels = read_message_table(romfs / 'message' / language / 'common' / 'option.tbl')
        has_on_off = (labels.get(bo.fnv1a64(bo.ON_LABEL)) == bo.ON_LABEL
                      and labels.get(bo.fnv1a64(bo.OFF_LABEL)) == bo.OFF_LABEL)
        check('game On/Off labels %s' % language, has_on_off)
        check('our label exists in the game %s' % language, not ours & set(labels))
    layout = (romfs / 'app' / 'battle' / 'bin' / 'layout_setting_battle_top_00.bin').read_bytes()
    for name in LAYOUT_PANES:
        check('pane hash in the layout file %s' % name, struct.pack('<Q', bo.pane_hash(name)) in layout)


def main():
    test_value_labels()
    test_row_text()
    test_labels()
    test_spec()
    test_encoders()
    test_pane_hashes()
    test_scratch_slots()
    test_page_button()
    with tempfile.TemporaryDirectory() as temp_dir:
        ips_path = Path(temp_dir) / 'test_options.ips'
        test_ips32(ips_path)
        built = test_full_builds(ips_path)
    romfs = os.environ.get('WB_ROMFS')
    if romfs:
        test_game_files(Path(romfs))
    finish('options layer: %d checks (%d option builds%s)' % (checks_run(), built, ', romfs' if romfs else ''))


if __name__ == '__main__':
    main()
