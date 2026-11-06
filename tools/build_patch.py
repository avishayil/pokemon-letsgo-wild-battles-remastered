#!/usr/bin/env python3
"""
build_patch.py - generate the Wild Battles Remastered mod for Pokemon: Let's Go, Pikachu! or Eevee! 1.0.2.

What this produces
------------------
One IPSwitch .pchtxt (and, on request, the same records as an Atmosphere IPS32 file) that turns the
game's overworld catch encounters into turn-based wild battles with the game's own EXP, catching,
optional in-game settings and the extras. Every record is generated here from the game's own code
(--text text.bin); nothing is hand-written. The patch is built from:
  * the built-in base (base_self_records): the encounter starts the game's battle-then-catch event;
  * modules (module_table): small code caves, each entered from one hook site in the game's code,
    plus a few in-place rewrites; and
  * the in-game settings layer (tools/build_options.py), which gates the modules at runtime.
Before writing, every cave is decoded with capstone and checked: it returns where it should, it
replays the instruction its hook displaced, and its stack is balanced on every path.

The address convention
----------------------
Eden/IPSwitch pchtxt record addresses are FILE offsets into the decompressed NSO, which includes the
0x100-byte NSO header:

        pchtxt_record_address = memory_offset + 0x100

All analysis (disassembly of text.bin, branch targets, adrp pages) is done in MEMORY-offset space; only
the emitted record address gets +0x100. Getting this wrong makes a cave land 0x100 low, on top of live
code.

Inputs and usage
----------------
  text.bin: the carved .text of the decompressed NSO (memory offset == file offset), from your own dump
            (see docs/BUILDING.md).

  python3 -I tools/build_patch.py --text text.bin --out-unified wild_battles_remastered.pchtxt

`--help` lists every option. Requires capstone (pip install capstone) for the self-checks.
"""
import argparse, struct, sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))       # `python -I` leaves the script's own folder off sys.path

from asm import (                            # the instruction encoders (tools/asm.py)
    EQ, NE, LO, HI, RET, NOP, STP_1617, LDP_1617, STP_910, LDP_910,
    sign_extend,
    ldr_x, str_x, ldr_w, str_w, ldrh_w, strh_w, ldrb_w, strb_w, ldrsw_i, ldr_x_pre, ldrh_post, ldrb_post,
    str_w_reg, stp_off, stp_pre, ldp_off, ldp_post,
    movz_w, movk_w, movz_x, mov_x, addi_w, add_xi, subs_wi, add_x_reg, add_lsl2,
    cmp_w, cmp_i, tst_w, orr_w, bic_w, lslv_w, lsli_w, csel_w, cset_ls,
    b, bl_, blr_, cbz_x, Asm,
)

try:
    from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
    from capstone.arm64 import ARM64_OP_IMM, ARM64_OP_MEM
    MD = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    MD_DETAIL = Cs(CS_ARCH_ARM64, CS_MODE_ARM)   # with operand details (slower; only where needed)
    MD_DETAIL.detail = True
except ImportError:
    print("need capstone: pip install capstone", file=sys.stderr)
    sys.exit(1)

NSOBID = "C208DB6A4EF4361F28B8402BE0F7CD9A00000000000000000000000000000000"
PCHTXT_OFF = 0x100  # pchtxt record addr = mem offset + 0x100

# ----- memory offsets of the hook sites + the code cave (from static analysis) -----
HOOK_A    = 0x28BF34   # state-4 handler: ldr x20,[x1,#0x60]   (clear instant-end flag)
HOOK_C    = 0xA9C650   # model-joint fn entry: sub sp,sp,#0x70  (missing-resource guard)
HOOK_ST   = 0x28BD20   # battle-event update entry: sub sp,sp,#0x70 (reset + result rewrite)
HOOK_P    = 0x9D8A30
HOOK_M    = 0x3059B8
HOOK_R    = 0x9E41D0   # BTL_POKEPARAM reflect fn entry: str x23,[sp,#-0x40]! (wild HP capture)
HOOK_E    = 0x319EC4   # after-battle processor (entry 0x319A60): str w8,[x19,#0x68] next-state write
HOOK_N    = 0xAA0D4C
HOOK_X    = 0xA67E30
HOOK_XF   = 0x82D3F0   # CATCH-EXP FORMULA (Ghidra 2026-11-04): computes one mon's catch EXP (level x base
                       #   /18, min 1; trade/affection/Lucky-Egg/buddy multipliers; catch-quality bonus)
                       #   and returns it in w0. Its ONLY 4 callers are the catch-reward subsystem: result
                       #   setup 0x827af0 (+0x64), the 'got N Exp. Points!' message 0x830118, the apply
                       #   machine state 0 0x830828, and the per-mon step 0x8315e4 (amount==0 -> state 9,
                       #   skips AddExp 0x8318c0). Returning 0 = no catch EXP for anyone. Battle EXP
                       #   never reaches it (live: 0x8318c0 counter unchanged across a wild KO).
HOOK_XG   = 0x830410   # CATCH-EXP 'can anyone gain EXP?' gate (Ghidra 2026-11-04): the catch-reward scene
                       #   (0x82f9c0, state +0xA8) calls this once (0x830074); true -> state 2 ("Your
                       #   Pokemon got N Exp. Points!" + apply + gauge), false -> state 10 = the game's
                       #   own no-EXP path (all-L100/fainted party): no message, no gauge. Returning
                       #   false drops the whole EXP screen. XF still covers the state-1 [desc+4]!=0
                       #   branch that jumps straight to the per-mon step machine.
HOOK_UG   = 0xB78AC    # resource-handle helper 0xb78a0 (+0xC): reads [x0+0x78] with no null check. Before
                       # "Play with Pikachu/Eevee" is unlocked, a battle-intro sequence command (0xa9dd00,
                       # run by the bseq interpreter 0x210510) passes it NULL -> unmapped read @0x78 and the
                       # game stops (found live on an early Eevee save, 2026-11-05). In place, 0 cave bytes.
HOOK_UH   = 0x1557D0   # model flag setter 0x1557d0 entry: ldr x8,[x0,#0xf8] with no null check. The same intro
                       # command's "nothing found" path calls it as (0, 0) -> unmapped read @0xf8 (live,
                       # early Eevee save, after UG). Jumps into the 3 padding words after its ret (UHP).
HOOK_UHP  = 0x155824   # the 3 udf #0 padding words after 0x1557d0's ret (unreferenced; checked both games)
# Three more helpers the same intro command (0xa9dd00, the "buddy_onoff" partner step) calls with a
# NULL object before the unlock; each dereferences it at once. Same in-place guard as UG: drop the unused
# frame-pointer setup, add cbz x0 -> the helper's epilogue (a NULL object = do nothing).
HOOK_UI   = 0x159748   # fn 0x159740 +0x8  (reads [x0+0x98]);   epilogue 0x159844
HOOK_UJ   = 0xB6C90    # fn 0xb6c80  +0x10 (reads [x0+0x78]; its not-found branch reads it too); epilogue 0xb6e1c
HOOK_UK   = 0xB64B4    # fn 0xb64a0  +0x14 (the buddy_onoff setter, reads [x0+0x78]); epilogue 0xb663c
HOOK_UM   = 0xA98B94   # 2nd intro buddy command step 0xa98b40, right after it fetches the partner model
                       # (0xa9c570 -> [sp+0x20]): before the unlock the model is NULL and the step uses it
                       # many times (0x5ed970 reads [x0+0x2e0] first). A NULL model -> the step's own early
                       # return 0xa9905c. Room: mov w8,#1 + str wzr,[sp,#0x1c] + strb w8,[sp,#0x18] become
                       # mov w8,#1 + str x8,[sp,#0x18] (byte 0x18 = 1 as before; 0x19..0x1f were leftovers).
HOOK_KM   = 0x14BCDC
HOOK_KS   = 0x14B8A0
HOOK_K    = 0xAB1E34
HOOK_G    = 0xAAF180
HOOK_WCH  = 0xAC05A0   # per-button menu update (0xAC0570): mov x0,x21 — x21 = that slot's battle parameters
HOOK_WCA  = 0xAB1FE8   # id-7 handler: mov w8,#0xa (the catch is committed as action 4 = RUN, through the
                       #   id-5 jump-table entry; see cave_xcatch_action)
HOOK_WCV  = 0xAB3194   # installer visibility const: mov w8,#0xc00 (never patched; the tests check that)
WC_NOP1, WC_NOP2 = 0x28C890, 0x28C8A0   # two battle-event setup writes (never patched; the tests check that)
RODATA_CLS_ENTRY = (0xF981D0, 0xF981BC, 0xFFB0CCE8)  # (addr, table base, ORIGINAL entry: kind6 -> 0xAA4EA4)
RODATA_DEC_ENTRY = (0xF98250, 0xF9823C, 0xFFB19DB8)  # (addr, table base, ORIGINAL entry: id6 -> 0xAB1FF4)

CAVE_BASE = 0xD32BA8   # start of the loader-provided .text page padding (zero-filled, RX)
SCRATCH   = 0x1EE0F00  # safe .bss-tail scratch page (module+0x1EE0F00..0x1EE1000)
CAVE_END_PAGE = 0xD33000  # the .text padding page ends here; caves must stay below it

PREIMAGE = {
    HOOK_A:  0xF9403034,  # ldr x20,[x1,#0x60]
    HOOK_C:  0xD101C3FF,  # sub sp,sp,#0x70
    HOOK_ST: 0xD101C3FF,  # sub sp,sp,#0x70
    HOOK_P:  0xF9404008,  # ldr x8,[x0,#0x80]
    HOOK_R:  0xF81C0FF7,  # str x23,[sp,#-0x40]!
    HOOK_E:  0xB9006A68,  # str w8,[x19,#0x68]
    HOOK_M:  0xD63F0100,
    HOOK_N:  0xF9000A76,  # str x22,[x19,#0x10]
    HOOK_X:  0x3900F7E8,  # strb w8,[sp,#0x3d]
    HOOK_XF:   0xFC1B0FE8,  # str d8,[sp,#-0x50]!      (catch-EXP formula 0x82d3f0, in-place word 0)
    HOOK_XF+4: 0xA9015FF8,  # stp x24,x23,[sp,#0x10]   (in-place word 1)
    HOOK_XG:   0xF81C0FF7,  # str x23,[sp,#-0x40]!     (catch-EXP gate 0x830410, in-place word 0)
    HOOK_XG+4: 0xA90157F6,  # stp x22,x21,[sp,#0x10]   (in-place word 1)
    HOOK_UG:      0x910083FD,  # add x29,sp,#0x20   (fn 0xb78a0 +0xC; x29 is not used by the body)
    HOOK_UG+0x4:  0xAA0803F3,  # mov x19,x8
    HOOK_UG+0x8:  0xF900027F,  # str xzr,[x19]      (output = empty)
    HOOK_UG+0xC:  0xF9403C08,  # ldr x8,[x0,#0x78]  (<- the null read)
    HOOK_UG+0x10: 0xB40003C8,  # cbz x8,#0xb7934    (existing "nothing found" exit -> epilogue)
    HOOK_UH:      0xF9407C08,  # ldr x8,[x0,#0xf8]  (fn 0x1557d0 entry)
    HOOK_UHP:     0x00000000,  # udf #0 padding after its ret
    HOOK_UHP+0x4: 0x00000000,  # udf #0
    HOOK_UHP+0x8: 0x00000000,  # udf #0
    HOOK_UI:      0x910043FD,  # add x29,sp,#0x10
    HOOK_UI+0x4:  0xAA0003E8,  # mov x8,x0
    HOOK_UI+0x8:  0xF9400020,  # ldr x0,[x1]
    HOOK_UJ:      0x910103FD,  # add x29,sp,#0x40
    HOOK_UJ+0x4:  0xAA0003F4,  # mov x20,x0
    HOOK_UJ+0x8:  0xF9403E88,  # ldr x8,[x20,#0x78]
    HOOK_UK:      0x910143FD,  # add x29,sp,#0x50
    HOOK_UK+0x4:  0xAA0003F6,  # mov x22,x0
    HOOK_UK+0x8:  0xF9403EC8,  # ldr x8,[x22,#0x78]
    HOOK_UM:      0x320003E8,  # mov w8,#1 (orr form)
    HOOK_UM+0x4:  0x910063E1,  # add x1,sp,#0x18
    HOOK_UM+0x8:  0xB9001FFF,  # str wzr,[sp,#0x1c]
    HOOK_UM+0xC:  0x390063E8,  # strb w8,[sp,#0x18]
    HOOK_KM: 0xF9000E62,  # str x2,[x19,#0x18]
    HOOK_KS: 0x6DBB23E9,  # stp d9,d8,[sp,#-0x50]!  (shared input helper entry; --input-source all)
    HOOK_K:  0xB9400414,  # ldr w20,[x0,#4]
    HOOK_WCH: 0xAA1503E0, # mov x0,x21
    HOOK_WCA: 0x52800148, # mov w8,#0xa
    HOOK_WCV: 0x321607E8, # mov w8,#0xc00  (orr-immediate form)
    WC_NOP1:  0x39161288, # strb w8,[x20,#0x584]
    WC_NOP2:  0x32190108, # orr w8,w8,#0x80
    HOOK_G:  0xA9BD57F6,  # stp x22,x21,[sp,#-0x30]!
}

BASE_PREIMAGE = {
    0x28BE84: 0xB9406A68,  # ldr w8,[x19,#0x68]
    0x28BF1C: 0xB4001494,  # cbz x20,#0x28c1ac
    0x28C1A0: 0x321F03E3,  # orr w3,wzr,#2 (mov w3,#2)
    0x28CB10: 0x52800142,  # mov w2,#0xa
    0x2FDF34: 0x94001E8F,  # bl 0x305970 (original call to fn B)
    0x2FDF80: 0x94001E7C,  # bl 0x305970 (original call to fn B)
    0x3057B8: 0xF9412514,  # ldr x20,[x8,#0x248]
    0x305934: 0x97FFC2CF,  # bl 0x2f6470 (descriptor builder — repointed to the routine)
    0x319FF4: 0x2A1F03F8,  # mov w24,wzr
    0x305970: 0xD10303FF,  # sub sp,sp,#0xc0 (fn B entry — replaced by the routine)
}

# ----- partner ("buddy") handle helpers: NULL guards for wild battles before the partner unlock -------
# Before "Play with Pikachu/Eevee" is unlocked the partner's buddy object does not exist, and the battle
# intro's buddy commands pass a NULL handle to this family of small helpers (all share the validity check
# [h+0x78] / [h+0x80] / refcount +0x54). Each guard makes a NULL handle return 0 / do nothing through the
# helper's own exit; a real handle runs exactly as before. All in place (0 cave bytes): the unused
# frame-pointer setup gives the slot, or 3 unreferenced padding words do (tramp). The table is generated
# from text.bin by an analysis that checked, per helper: x29 unused in the body, no branch into the
# rewritten words, one epilogue. UG/UJ/UK above are
# three more members, written first. Kinds:
#   mov2    [add x29][mov rd,x0]                   -> [mov rd,x0][cbz x0,exit]
#   sret5   [add x29][mov x19,x8][str xzr,[x19]][ldr x8,[x0,#0x78]][cbz x8,exit]
#                                                  -> [mov x19,x8][str xzr,[x19]][cbz x0,exit][ldr][cbz x8,exit]
#   direct2 [add x29][ldr x8,[x0,#0x78]]           -> [cbz x0,exit][ldr x8,[x0,#0x78]]
#   b4ff0   [add x29][mov x19,x1][mov x20,x0]      -> [mov x19,x1][mov x20,x0][cbz x0,exit]
#   tramp   entry [sub sp,..] -> b pad; pad (3 udf words before the entry): [cbz x0,ret][sub sp,..][b entry+4]
BUDDY_GUARDS = [   # (kind, fn entry, patch site, exit, original words at the site, reg)  GENERATED, see docstring
    ('mov2',   0x0B5290, 0x0B52A8, 0x0B547C, (0x910143FD, 0xAA0003F5), 21),
    ('mov2',   0x0B5570, 0x0B5580, 0x0B5648, (0x9100C3FD, 0xAA0003F4), 20),
    ('mov2',   0x0B56A0, 0x0B56B0, 0x0B5780, (0x910103FD, 0xAA0003F6), 22),
    ('mov2',   0x0B58F0, 0x0B590C, 0x0B5B50, (0x9101C3FD, 0xAA0003F5), 21),
    ('mov2',   0x0B5FF0, 0x0B6004, 0x0B60D8, (0x910103FD, 0xAA0003F5), 21),
    ('mov2',   0x0B6720, 0x0B6738, 0x0B68C4, (0x910143FD, 0xAA0003F5), 21),
    ('mov2',   0x0B68F0, 0x0B6904, 0x0B6A8C, (0x910143FD, 0xAA0003F6), 22),
    ('mov2',   0x0B6AC0, 0x0B6AD4, 0x0B6C58, (0x910103FD, 0xAA0003F5), 21),
    ('mov2',   0x0B6E50, 0x0B6E60, 0x0B6FD0, (0x910103FD, 0xAA0003F4), 20),
    ('mov2',   0x0B7000, 0x0B7014, 0x0B719C, (0x910143FD, 0xAA0003F4), 20),
    ('mov2',   0x0B71C0, 0x0B71D4, 0x0B7358, (0x910143FD, 0xAA0003F5), 21),
    ('mov2',   0x10BCB0, 0x10BCB8, 0x10BCE0, (0x910043FD, 0xAA0003F3), 19),
    ('mov2',   0x10C030, 0x10C038, 0x10C060, (0x910043FD, 0xAA0003F3), 19),
    ('sret5',  0x0B54C0, 0x0B54CC, 0x0B5554, (0x910083FD, 0xAA0803F3, 0xF900027F, 0xF9403C08, 0xB40003C8), 19),
    ('sret5',  0x0B6670, 0x0B667C, 0x0B6704, (0x910083FD, 0xAA0803F3, 0xF900027F, 0xF9403C08, 0xB40003C8), 19),
    ('sret5',  0x0B7950, 0x0B795C, 0x0B79E4, (0x910083FD, 0xAA0803F3, 0xF900027F, 0xF9403C08, 0xB40003C8), 19),
    ('direct2', 0x0B57D0, 0x0B57E0, 0x0B58A8, (0x910103FD, 0xF9403C08), 0),
    ('direct2', 0x0B6130, 0x0B6140, 0x0B61F8, (0x9100C3FD, 0xF9403C08), 0),
    ('direct2', 0x0B63A0, 0x0B63AC, 0x0B6464, (0x9100C3FD, 0xF9403C08), 0),
    ('b4ff0',  0x0B4FF0, 0x0B5000, 0x0B50E4, (0x910103FD, 0xAA0103F3, 0xAA0003F4), 20),
    ('tramp',  0x0B5BC0, 0x0B5BC0, 0x0B5FB0, (0xD10683FF,), 0),
]
BUDDY_KEYS = []
for _i, (_k, _f, _s, _e, _ws, _rd) in enumerate(BUDDY_GUARDS):
    for _j, _w in enumerate(_ws):
        PREIMAGE[_s + 4 * _j] = _w
    BUDDY_KEYS.append('UB%02d' % _i)
    if _k == 'tramp':
        for _j in range(3):
            PREIMAGE[_f - 0xC + 4 * _j] = 0x00000000     # udf #0 padding before the entry
        BUDDY_KEYS.append('UB%02dP' % _i)

def _buddy_build(kind, exit_, words, rd, part=''):
    def build(text, base):
        helper_exit = game_addr(exit_)
        if kind == 'mov2':
            return [mov_x(rd, 0), cbz_x(0, base + 4, helper_exit)]
        if kind == 'sret5':
            return [mov_x(19, 8), str_x(31, 19, 0), cbz_x(0, base + 8, helper_exit), ldr_x(8, 0, 0x78), cbz_x(8, base + 16, helper_exit)]
        if kind == 'direct2':
            return [cbz_x(0, base, helper_exit), ldr_x(8, 0, 0x78)]
        if kind == 'b4ff0':
            return [mov_x(19, 1), mov_x(20, 0), cbz_x(0, base + 8, helper_exit)]
        if kind == 'tramp' and part == '':
            return [b(base, base - 0xC)]                             # entry -> the padding
        if kind == 'tramp' and part == 'P':
            return [cbz_x(0, base, helper_exit), words[0], b(base + 8, base + 0xC + 4)]   # NULL -> ret; else replay + back
        raise ValueError(kind)
    return build

def buddy_modules():
    out = []
    for i, (kind, f, site, exit_, words, rd) in enumerate(BUDDY_GUARDS):
        out.append(dict(key='UB%02d' % i, hook=game_addr(site), build=_buddy_build(kind, exit_, words, rd),
                        variants={'addon', 'main', 'catch'}, inplace=True, freeform=True,
                        scratch='none; NULL-safe buddy handle helper %#x (%s)' % (f, kind), scratch_slots=()))
        if kind == 'tramp':
            out.append(dict(key='UB%02dP' % i, hook=game_addr(f) - 0xC, build=_buddy_build(kind, exit_, words, rd, 'P'),
                            variants={'addon', 'main', 'catch'}, inplace=True, freeform=True,
                            scratch='none; NULL check in the padding before %#x' % f, scratch_slots=()))
    return out

# ----- game profiles (Let's Go, Pikachu! 1.0.2 = the reference; Let's Go, Eevee! 1.0.2) -------------
# Every address in this file is written for Pikachu. Both games are built from the same source and
# Eevee's code sits at small, piecewise-constant shifts (tools/game_maps.py, generated from both games'
# code). set_game('eevee') re-points every address constant and the two expected-word
# tables through game_addr(); code that names a game address inline wraps it in game_addr(). Branch-type expected
# words are re-encoded for their new address (reloc_word). Rodata, data, bss and the end-of-text
# padding keep their addresses in both games. The default stays Pikachu, so nothing changes for it.
import hashlib as _hashlib
import game_maps as _game_maps                # the Pikachu -> Eevee address shift table (generated)
import build_options as _bo                   # the in-game settings layer (tools/build_options.py)
_bo.bind(sys.modules[__name__])

GAME_INFO = {
    'pikachu': dict(title="Let's Go, Pikachu!", title_id='010003F003A34000', text_sha256=_game_maps.PIKACHU_TEXT_SHA256,
                    nsobid="C208DB6A4EF4361F28B8402BE0F7CD9A00000000000000000000000000000000",
                    partner_kind=1),
    'eevee': dict(title="Let's Go, Eevee!", title_id='0100187003A36000', text_sha256=_game_maps.EEVEE_TEXT_SHA256,
                  nsobid="5831EC64D6B696FD69E69505821B97B100000000000000000000000000000000",
                  partner_kind=2),
}
GAME = 'pikachu'
OPTIONS = None              # the normalized options layer of the current layout (build_options.normalize)
_TEXT_END_PIKA = 0xD32BA8
_ADDR_MIN = 0x10000          # game code starts well above this; scratch/struct offsets are all < 0x1000

def game_addr(a):
    """Pikachu address -> the same place in the current game (identity for Pikachu)."""
    if GAME == 'pikachu' or a >= _TEXT_END_PIKA:
        return a
    for s, e, d in _game_maps.EEVEE_RUNS:
        if s <= a < e:
            return a + d
    raise ValueError("address %#x is not in a mapped region for %s" % (a, GAME))

def game_of_text(text):
    """'pikachu' / 'eevee' from text.bin's SHA-256; None if it is neither 1.0.2 build."""
    h = _hashlib.sha256(text).hexdigest()
    for k, v in GAME_INFO.items():
        if v['text_sha256'] == h:
            return k
    return None

def reloc_word(w, a):
    """Re-encode an instruction found at Pikachu address a for its address in the current game: a
    PC-relative branch keeps pointing at the same (mapped) target; anything else is unchanged."""
    na = game_addr(a)
    if (w & 0x7C000000) == 0x14000000:                                   # B / BL
        t = a + sign_extend(w & 0x3FFFFFF, 26) * 4
        return (w & 0xFC000000) | (((game_addr(t) - na) >> 2) & 0x3FFFFFF)
    if (w & 0xFF000010) == 0x54000000 or (w & 0x7E000000) == 0x34000000:  # B.cond / CBZ / CBNZ
        t = a + sign_extend((w >> 5) & 0x7FFFF, 19) * 4
        return (w & 0xFF00001F) | ((((game_addr(t) - na) >> 2) & 0x7FFFF) << 5)
    if (w & 0x7E000000) == 0x36000000:                                   # TBZ / TBNZ
        t = a + sign_extend((w >> 5) & 0x3FFF, 14) * 4
        return (w & 0xFFF8001F) | ((((game_addr(t) - na) >> 2) & 0x3FFF) << 5)
    assert (w & 0x1F000000) != 0x10000000 and (w & 0x3B000000) != 0x18000000, (
        "reloc_word: ADR/ADRP/LDR-literal expected word not supported", hex(a), hex(w))
    return w

_PIKA = {}
# Module constants with these prefixes are game addresses: set_game re-points them for the game built.
REMAPPED_PREFIXES = ('HOOK_', 'ROUTINE_', 'WC_', 'RODATA_', 'ID5_', 'CMD_')

def set_game(name):
    """Point every game address constant at `name`'s build. Idempotent; 'pikachu' restores the reference."""
    global GAME, NSOBID, PREIMAGE, BASE_PREIMAGE
    assert name in GAME_INFO, ("unknown game", name)
    g = globals()
    if not _PIKA:
        for k, v in list(g.items()):
            if not k.startswith(REMAPPED_PREFIXES):
                continue
            if (isinstance(v, int) and _ADDR_MIN <= v < _TEXT_END_PIKA) or (isinstance(v, tuple) and k.startswith('RODATA_')):
                _PIKA[k] = v
        # every other upper-case int constant that looks like a .text address must be one of the
        # known non-remapped ones; a new address constant with another name would fail here
        stray = [k for k, v in g.items() if k.isupper() and not k.startswith('_') and isinstance(v, int) and not isinstance(v, bool)
                 and _ADDR_MIN <= v < _TEXT_END_PIKA and v % 4 == 0 and k not in _PIKA]
        assert not stray, ("set_game: address-like constants not covered by a remap prefix", stray)
        _PIKA['_PREIMAGE'], _PIKA['_BASE_PREIMAGE'] = dict(PREIMAGE), dict(BASE_PREIMAGE)
    GAME = name
    for k, v in _PIKA.items():
        if k.startswith('_'):
            continue
        if k.startswith('RODATA_'):                      # (entry addr, table base[, ORIGINAL s32 entry])
            if len(v) == 3:
                tgt = v[1] + sign_extend(v[2], 32)
                g[k] = (v[0], v[1], (game_addr(tgt) - v[1]) & 0xFFFFFFFF)
            else:
                g[k] = v
        else:
            g[k] = game_addr(v)
    PREIMAGE = {game_addr(a): reloc_word(w, a) for a, w in _PIKA['_PREIMAGE'].items()}
    BASE_PREIMAGE = {game_addr(a): reloc_word(w, a) for a, w in _PIKA['_BASE_PREIMAGE'].items()}
    NSOBID = GAME_INFO[name]['nsobid']


# adrp x16, module+0x1EE0000 then add x16,x16,#0xf00 -> x16 = module+0x1EE0F00
# (adrp is PC-relative from the cave's page 0xD32000; 0xD0008D70 reaches 0x1EE0000)
ADRP_SCRATCH = 0xD0008D70
ADD_F00      = 0x913C0210
ADRP_SCRATCH_X9 = (ADRP_SCRATCH & ~31) | 9   # same page reach, into x9 (when x16 is busy)
ADD_F00_X9      = 0x91000000 | (0xF00 << 10) | (9 << 5) | 9

MARKER_WORD = 0xB9029A89
def disp(text, mem):  # the original instruction word displaced by a hook
    if mem == HOOK_M:
        return MARKER_WORD
    return struct.unpack_from('<I', text, mem)[0]

def ret_branch(body, base, target):
    return b(base + len(body)*4, target)

# ----- the scratch page -------------------------------------------------------------------------------
# 256 bytes at SCRATCH that the modules share (the tail of .bss). A cave reaches it in one of two ways:
#   x16 = SCRATCH        (adrp + add #0xF00, CaveAsm.scratch_base): use the slot offsets as they are;
#   x16 = SCRATCH's page (adrp only, CaveAsm.scratch_page):        use scratch_offset(slot).
# Each module lists the slots it owns in its registry entry (scratch_slots); verify_scratch_use checks
# every build against that list.
SCRATCH_PAGE_ADDRESS = SCRATCH & ~0xFFF
SCRATCH_IN_PAGE = SCRATCH - SCRATCH_PAGE_ADDRESS        # 0xF00
SCRATCH_SIZE = 0x100      # usable bytes: +0x00..+0xFF. .bss ends at SCRATCH + 0x100: nothing past it is mapped

def scratch_offset(slot):
    """The offset of `slot` from SCRATCH's 4 KB page (for a register that holds the page)."""
    return SCRATCH_IN_PAGE + slot

SLOT_FAINT_CLEARS = 0x34      # u32 count: instant-end flags cleared (module A)
SLOT_SHARE_ZEROED = 0x38      # u32 count: Exp. Share bytes zeroed (module X)
SLOT_RESULT_STATE = 0x40      # u32: the battle-result machine's last state (ST)
SLOT_STATES_SEEN = 0x44       # u32: bit n set = state n was seen (ST)
SLOT_BATTLE = 0x48            # u64: the battle object (ST; for debugging over the GDB stub)
SLOT_RESULT_OBJECT = 0x50     # u64: its result object (ST; for debugging)
SLOT_RESULT = 0x58            # u32: the last battle result, 3 = ran away (written by ST, read by E)
SLOT_RESULT_ENTRIES = 0x5C    # u32 count: ST entries
SLOT_NO_RECALL_RUNS = 0x6C    # u32 count: recall commands neutralized (N)
SLOT_BUTTONS = 0x80           # u64: the held buttons, mirrored from the input poller (KM)
SLOT_XCATCH_UNLOCK = 0xA0     # u8: 1 = the Y catch is allowed at this command menu (x-anytime)
SLOT_JOINT_ENTRIES = 0xB0     # u32 count: joint-guard entries in a wild battle (C)
SLOT_WILD_PARAM = 0xB8        # u64: the wild Pokemon's battle parameters (WCH; read by WCA and OQG)
SLOT_WILD_HP = 0xC8           # u32: the wild Pokemon's current HP (R)
SLOT_WILD_SPECIES = 0xCC      # u32: its species (R)
SLOT_WILD_MAX_HP = 0xD0       # u32: its max HP (R)
SLOT_WILD_SLOT = 0xD4         # u8: its battle slot, 12 for the wild opponent (R)
SLOT_JOINT_GUARD_HITS = 0xD8  # u32 count: models the joint guard answered "no joint" for (C)
SLOT_CATCH_INTENT = 0xDC      # u8: 1 = the player chose to catch (K / WCA set it, E consumes it)
SLOT_KO_REWRITES = 0xE0       # u32 count: KO results rewritten (ST)
SLOT_EXP_GATE_HITS = 0xE4     # u32 count: battles made EXP-eligible (P)
SLOT_PARTNER_FORCED = 0xF0    # u8: 1 = the mod enabled the partner for this battle (UE)
SLOT_WILD_ENCOUNTER = 0xF8    # u8: 1 = this battle is one of the mod's wild battles (M sets, E clears)
SLOT_ARMED = 0xFC             # u8: the instant-end flag was cleared in this battle (A)
SLOT_KO_DONE = 0xFD           # u8: the KO result was already rewritten (ST, one-shot)
SLOT_HAVE_WILD = 0xFE         # u8: R recorded the wild Pokemon (HP/species/slot are valid)
SLOT_EVOLVE_PENDING = 0xFF    # u8: run the evolution check after this battle (ST sets, E consumes)

class CaveAsm(Asm):
    """asm.Asm plus the idioms the caves here share."""

    def scratch_base(self, reg=16):
        """x`reg` = SCRATCH, so a slot is a plain offset."""
        self.adrp(reg, SCRATCH_PAGE_ADDRESS)
        self.w(add_xi(reg, reg, SCRATCH_IN_PAGE))

    def scratch_page(self, reg=16):
        """x`reg` = SCRATCH's 4 KB page; address a slot with scratch_offset(slot)."""
        self.adrp(reg, SCRATCH_PAGE_ADDRESS)

    def increment(self, offset, reg=17, base=16):
        """u32 [x`base`+offset] += 1, through w`reg`."""
        self.w(ldr_w(reg, base, offset), addi_w(reg, reg, 1), str_w(reg, base, offset))

    def replay_and_return(self, text, hook):
        """The instruction the hook displaced, then a branch back to the one after it."""
        self.w(disp(text, hook))
        self.b(hook + 4)

# ----- the code caves (bodies in memory-offset space at CAVE_BASE+) -----
TIMER_WORD_INDEX = 14     # cave_faint_and_timer's timer word (with the Battle Timer row: a branch to OTM)

def cave_faint_and_timer(text, base, timer=True, timer_gate=False):
    cave = CaveAsm(base)
    cave.w(STP_910, STP_1617)
    cave.scratch_base()
    cave.w(ldrb_w(17, 16, SLOT_WILD_ENCOUNTER))
    cave.cbz_w(17, 'out')                                       # not one of our wild battles
    cave.w(ldr_x(9, 1, 0x60))
    cave.cbz_x(9, 'out')                                        # no command menu yet
    cave.w(ldr_w(17, 19, 0x574), movz_w(10, 0x80), tst_w(17, 10))
    cave.bc(EQ, 'out')                                          # instant-end flag (0x80) not set
    cave.w(bic_w(17, 17, 10), str_w(17, 19, 0x574))             # clear the instant-end flag
    if timer:
        cave.label('timer')
        assert cave.labels['timer'] == TIMER_WORD_INDEX
    if timer and timer_gate:                                    # Battle Timer row: OTM decides
        cave.w(_bo.timer_word(text, cave.pc()))
    elif timer:
        cave.w(strh_w(31, 19, 0x562))                           # clear the 300 s battle timer
    cave.increment(SLOT_FAINT_CLEARS)
    cave.w(movz_w(17, 1), strb_w(17, 16, SLOT_ARMED))
    cave.label('out')
    cave.w(LDP_1617, LDP_910)
    cave.replay_and_return(text, HOOK_A)
    return cave.words()

def cave_joint_guard(text, base):
    cave = CaveAsm(base)
    cave.w(STP_1617)
    cave.scratch_base()
    cave.w(ldrb_w(17, 16, SLOT_WILD_ENCOUNTER))
    cave.cbz_w(17, 'restore')                                   # not ours: the original, untouched
    cave.increment(SLOT_JOINT_ENTRIES)
    cave.w(LDP_1617)
    cave.cbz_x(0, 'original')                                   # no model
    cave.w(ldr_x(9, 0, 0x1E0))
    cave.cbnz_x(9, 'original')                                  # it has a resource
    cave.w(ldrb_w(9, 0, 0x192))
    cave.cbz_w(9, 'no_joint')
    cave.w(ldr_x(9, 0, 0x1E8))
    cave.cbnz_x(9, 'original')
    cave.label('no_joint')
    cave.w(STP_1617)
    cave.scratch_base()
    cave.increment(SLOT_JOINT_GUARD_HITS)
    cave.w(LDP_1617, movz_x(0, 0), RET)                         # x0 = 0: "no joint"
    cave.label('restore')
    cave.w(LDP_1617)
    cave.label('original')
    cave.replay_and_return(text, HOOK_C)
    return cave.words()

def cave_result_rewrite(text, base, strict_gate=True):  # entry hook: reset flags@state0; result 1->3 @state5 on VERIFIED KO (one-shot)
    cave = CaveAsm(base)
    cave.w(STP_910, STP_1617)
    cave.scratch_base()
    cave.w(ldr_w(17, 0, 0x68), str_w(17, 16, SLOT_RESULT_STATE))
    cave.cbnz_w(17, 'not_state_0')
    cave.w(str_w(31, 16, SLOT_ARMED))                           # state 0: clear armed/done/have/evolve (u32)
    cave.label('not_state_0')
    cave.w(cmp_i(17, 31))
    cave.bc(HI, 'record')
    cave.w(movz_w(9, 1), lslv_w(9, 9, 17), ldr_w(10, 16, SLOT_STATES_SEEN), orr_w(10, 10, 9),
           str_w(10, 16, SLOT_STATES_SEEN))                     # states seen |= 1 << state
    cave.label('record')
    cave.w(ldr_x(9, 0, 0x720), str_x(9, 16, SLOT_BATTLE))
    cave.cbz_x(9, 'done')
    cave.w(ldr_x(10, 9, 0x68), str_x(10, 16, SLOT_RESULT_OBJECT))
    cave.cbz_x(10, 'done')
    cave.w(ldr_w(17, 10, 0x508), str_w(17, 16, SLOT_RESULT))
    cave.w(ldr_w(9, 0, 0x68), cmp_i(9, 5))
    cave.bc(NE, 'done')                                         # state 5
    cave.w(cmp_i(17, 1))
    cave.bc(NE, 'done')                                         # result 1
    cave.w(ldrb_w(9, 16, SLOT_ARMED))
    cave.cbz_w(9, 'done')                                       # armed (our faint was forced)
    cave.w(ldrb_w(9, 16, SLOT_KO_DONE))
    cave.cbnz_w(9, 'done')                                      # not rewritten yet (one-shot)
    cave.w(ldrb_w(9, 16, SLOT_HAVE_WILD))
    cave.cbz_w(9, 'done')                                       # the wild HP was recorded
    cave.w(ldr_w(9, 16, SLOT_WILD_HP))
    cave.cbnz_w(9, 'done')                                      # wild HP == 0 (a real KO)
    cave.w(ldrb_w(9, 16, SLOT_WILD_ENCOUNTER))
    cave.cbz_w(9, 'done')                                       # still our wild battle
    cave.w(ldr_w(9, 16, SLOT_WILD_SPECIES))
    cave.cbz_w(9, 'done')                                       # a valid species
    if strict_gate:
        cave.w(ldrb_w(9, 16, SLOT_WILD_SLOT), cmp_i(9, 12))
        cave.bc(NE, 'done')                                     # the wild slot (12)
        cave.w(ldr_w(9, 16, SLOT_WILD_MAX_HP))
        cave.cbz_w(9, 'done')                                   # with a real max HP
    cave.w(movz_w(9, 3), str_w(9, 10, 0x508))                   # result = 3 ("fainted, no capture")
    cave.w(movz_w(9, 1), strb_w(9, 16, SLOT_KO_DONE), strb_w(9, 16, SLOT_EVOLVE_PENDING))
    cave.increment(SLOT_KO_REWRITES, reg=9)
    cave.label('done')
    cave.increment(SLOT_RESULT_ENTRIES)
    cave.w(LDP_1617, LDP_910)
    cave.replay_and_return(text, HOOK_ST)
    return cave.words()

BUDDY_FORCE = True          # main()/mod_spec set False for an external base (its records own that space)
_BUDDY_ON = True            # effective per layout (BUDDY_FORCE and 'UE' not omitted); read by cave_encounter_marker/cave_after_battle
_HELPER_UE = 0x305AAC        # 24 words, ends at 0x305b0c (< the count cave at 0x305b10)
PARTNER_KIND_OFFSET = 0x110   # in the save block: the partner kind byte (0 = no partner yet)

def ue_helper(text, base):  # base = game_addr(_HELPER_UE). Entry +0 = restore (w17 = 0), +4 = enable (w17 != 0)
    cave = CaveAsm(base)
    cave.w(movz_w(17, 0))                             # restore entry: mode 0
    cave.adrp(16, _bo.SINGLETON_PAGE)                 # enable entry (w17 = 1 from cave_encounter_marker)
    cave.w(ldr_x(16, 16, _bo.SINGLETON_OFF))          # the singleton holder (as the base reads it)
    cave.cbz_x(16, 'out')
    cave.w(ldr_x(16, 16, 0), ldr_x(16, 16, 0x98), ldr_x(16, 16, 0x58),
           ldr_x(16, 16, 0x78))                       # the save-block handle
    cave.cbz_x(16, 'out')
    cave.cbz_w(17, 'restore')
    cave.w(ldrb_w(17, 16, PARTNER_KIND_OFFSET))       # the partner-enabled byte
    cave.cbnz_w(17, 'out')                            # already enabled (a real unlock): leave it
    cave.w(movz_w(17, GAME_INFO[GAME]['partner_kind']),   # the game's own partner kind (1 Pikachu, 2 Eevee)
           strb_w(17, 16, PARTNER_KIND_OFFSET))       # enabled for this battle
    cave.scratch_page(16)
    cave.w(strb_w(17, 16, scratch_offset(SLOT_PARTNER_FORCED)), RET)   # remember that we did
    cave.label('restore')
    cave.scratch_page(17)
    cave.w(ldrb_w(17, 17, scratch_offset(SLOT_PARTNER_FORCED)))
    cave.cbz_w(17, 'out')                             # not ours: nothing to undo
    cave.w(strb_w(31, 16, PARTNER_KIND_OFFSET))       # disabled again
    cave.scratch_page(17)
    cave.w(strb_w(31, 17, scratch_offset(SLOT_PARTNER_FORCED)))
    cave.label('out')
    cave.w(RET)
    words = cave.words()
    assert len(words) == 24 and base + len(words) * 4 <= game_addr(0x305B10), "UE helper overflows into the count cave"
    return words

def cave_encounter_marker(text, base):
    cave = CaveAsm(base)
    cave.w(STP_1617)
    cave.scratch_page()
    cave.w(movz_w(17, 1), strb_w(17, 16, scratch_offset(SLOT_WILD_ENCOUNTER)))   # w17 = 1 is also UE's enable mode
    if _BUDDY_ON:
        cave.bl(game_addr(_HELPER_UE) + 4)            # UE: enable the partner for this battle
    cave.w(LDP_1617)
    cave.replay_and_return(text, HOOK_M)              # str w9,[x20,#0x298]
    return cave.words()

EE620_FRAMES      = 150            # u32 @ buffer+0xC
EE620_FP_OFF      = 0x44           # u32 inside the file ('ee62' of its camera path)
EE620_FP_VAL      = 0x32366565
EE620_POKE_VAL    = 9999           # > sequence length -> command never starts
EE620_POKES = [1628, 1712, 2004, 2080, 2116, 2152, 2444, 2520, 2556, 2592, 2636, 2680,
               2716, 2752, 3044, 3080, 3116, 3192, 3228, 3264, 3316, 3368, 3408, 3432,
               3472, 3508, 3544, 3580, 3616, 3908, 3984, 4036, 4328, 4404, 4456, 4532,
               4608, 4644]         # StartFrame file-offsets of the 38 recall commands

NORECALL_ENC = 'table'

def _ee620_delta_bytes():
    """u16(start) + one u8 delta (in 4-byte units) per gap + u8(0) sentinel. Asserts the scheme
    round-trips to EE620_POKES exactly."""
    out = bytearray(struct.pack('<H', EE620_POKES[0]))
    for i in range(1, len(EE620_POKES)):
        d = (EE620_POKES[i] - EE620_POKES[i-1]) // 4
        assert 0 < d < 256 and EE620_POKES[i-1] + d*4 == EE620_POKES[i], ("delta range", i, d)
    out += bytes((EE620_POKES[i] - EE620_POKES[i-1]) // 4 for i in range(1, len(EE620_POKES)))
    out += b'\x00'
    # round-trip check
    dec, cur = [struct.unpack_from('<H', out, 0)[0]], struct.unpack_from('<H', out, 0)[0]
    for delta in out[2:]:
        if delta == 0:
            break
        cur += delta * 4
        dec.append(cur)
    assert dec == EE620_POKES, "delta decode != EE620_POKES"
    return bytes(out)

def _no_recall_checks(cave):
    """Shared head of both no-recall encodings: our wild battle, the faint sequence loaded and
    fingerprinted; then w9 = the poke value and x10 = the poke table. Any miss -> 'out'."""
    cave.w(STP_1617, STP_910)
    cave.scratch_page()
    cave.w(ldrb_w(17, 16, scratch_offset(SLOT_WILD_ENCOUNTER)))
    cave.cbz_w(17, 'out')
    cave.w(ldr_x(16, 22, 0x170))                      # x16 = the loaded sequence buffer
    cave.cbz_x(16, 'out')
    cave.w(ldr_w(17, 16, 0x0C), cmp_i(17, EE620_FRAMES))
    cave.bc(NE, 'out')                                # its length is ee620's
    cave.w(movz_w(9, EE620_FP_VAL & 0xFFFF), movk_w(9, EE620_FP_VAL >> 16, 16),
           ldr_w(17, 16, EE620_FP_OFF), cmp_w(17, 9))
    cave.bc(NE, 'out')                                # and so is its fingerprint
    cave.w(movz_w(9, EE620_POKE_VAL))
    cave.adr(10, 'table')

def _no_recall_tail(cave, text):
    cave.label('done')
    cave.scratch_page(9)
    cave.increment(scratch_offset(SLOT_NO_RECALL_RUNS), base=9)
    cave.label('out')
    cave.w(LDP_910, LDP_1617)
    cave.replay_and_return(text, HOOK_N)
    cave.label('table')

def cave_no_recall(text, base):  # no-recall faint: poke ee620's recall commands in the loaded buffer
    cave = CaveAsm(base)
    _no_recall_checks(cave)
    if NORECALL_ENC == 'table':                       # u16 offsets, then a 0
        cave.label('loop')
        cave.w(ldrh_post(17, 10, 2))
        cave.cbz_w(17, 'done')
        cave.w(str_w_reg(9, 16, 17))                  # buffer[offset] = the poke value
        cave.b('loop')
        _no_recall_tail(cave, text)
        halves = EE620_POKES + [0]
        if len(halves) % 2:
            halves.append(0)
        cave.w(*[halves[i] | (halves[i + 1] << 16) for i in range(0, len(halves), 2)])
        return cave.words()
    # 'delta': w11 = the running offset, x10 walks the delta bytes
    cave.w(ldrh_post(11, 10, 2))                      # w11 = the first offset; x10 -> the deltas
    cave.label('loop')
    cave.w(str_w_reg(9, 16, 11),                      # buffer[offset] = the poke value
           ldrb_post(17, 10, 1))                      # w17 = the next delta (in words)
    cave.cbz_w(17, 'done')                            # 0 ends the list
    cave.w(add_lsl2(11, 11, 17))                      # offset += delta * 4
    cave.b('loop')
    _no_recall_tail(cave, text)
    data = _ee620_delta_bytes()
    data += b'\x00' * (-len(data) % 4)
    cave.w(*[struct.unpack_from('<I', data, i)[0] for i in range(0, len(data), 4)])
    return cave.words()

def cave_exp_gate(text, base):  # EXP eligibility, only for our wild battles with the base's battle profile
    cave = CaveAsm(base)
    cave.w(STP_1617)
    cave.scratch_base()
    cave.w(ldrb_w(17, 16, SLOT_WILD_ENCOUNTER))
    cave.cbz_w(17, 'original')
    cave.w(ldr_x(16, 0, 0x80))                        # setup = [x0+0x80]
    cave.cbz_x(16, 'original')
    cave.w(ldrb_w(17, 16, 0x4A4), cmp_i(17, 1))
    cave.bc(NE, 'original')                           # setup+0x4A4 == 1
    cave.w(ldr_w(17, 16, 0x494))
    cave.cbnz_w(17, 'original')                       # setup+0x494 == 0
    cave.scratch_base()
    cave.increment(SLOT_EXP_GATE_HITS)
    cave.w(LDP_1617, movz_w(0, 1), RET)               # eligible: w0 = 1
    cave.label('original')
    cave.w(LDP_1617)
    cave.replay_and_return(text, HOOK_P)
    return cave.words()

def cave_wild_reflect(text, base):
    cave = CaveAsm(base)
    cave.w(STP_1617)
    cave.scratch_base()
    cave.w(ldrb_w(17, 16, SLOT_WILD_ENCOUNTER))
    cave.cbz_w(17, 'out')
    cave.w(ldrb_w(17, 0, 0x87), cmp_i(17, 6))
    cave.bc(LO, 'out')                                # the opponent's side (slot >= 6)
    cave.w(strb_w(17, 16, SLOT_WILD_SLOT),
           ldrh_w(17, 0, 0x7E), str_w(17, 16, SLOT_WILD_HP),
           ldrh_w(17, 0, 0x78), str_w(17, 16, SLOT_WILD_SPECIES),
           ldrh_w(17, 0, 0x7C), str_w(17, 16, SLOT_WILD_MAX_HP),
           movz_w(17, 1), strb_w(17, 16, SLOT_HAVE_WILD))
    cave.label('out')
    cave.w(LDP_1617)
    cave.replay_and_return(text, HOOK_R)
    return cave.words()

def cave_after_battle(text, base, catch=False):  # after battle: next state 4 -> 1 (the evolution check) when evolve_pending
    cave = CaveAsm(base)
    cave.w(STP_1617)
    if not catch:
        cave.scratch_base()
        cave.w(ldrb_w(17, 16, SLOT_EVOLVE_PENDING))
        cave.cbz_w(17, 'clear')
        cave.w(cmp_i(8, 4))
        cave.bc(NE, 'clear')                          # only the would-skip (state 4) transition
        cave.w(movz_w(8, 1))                          # -> state 1: the evolution check
        cave.label('clear')
        cave.w(strb_w(31, 16, SLOT_EVOLVE_PENDING),   # one-shot
               strb_w(31, 16, SLOT_WILD_ENCOUNTER))   # the battle is over
    else:
        cave.scratch_page()
        cave.w(cmp_i(8, 4))
        cave.bc(NE, 'clear')                          # only the would-end (state 4) transition
        cave.w(ldrb_w(17, 16, scratch_offset(SLOT_CATCH_INTENT)))
        cave.cbz_w(17, 'evolve')
        cave.w(ldr_w(17, 16, scratch_offset(SLOT_RESULT)), cmp_i(17, 3))
        cave.bc(NE, 'evolve')                         # ran away (a KO logs 1: evolution wins)
        cave.w(movz_w(8, 3))                          # -> state 3: the native capture
        cave.b('clear')
        cave.label('evolve')
        cave.w(ldrb_w(17, 16, scratch_offset(SLOT_EVOLVE_PENDING)))
        cave.cbz_w(17, 'clear')
        cave.w(movz_w(8, 1))                          # -> state 1: the evolution check
        cave.label('clear')                           # every battle: clear the per-battle flags
        cave.w(strb_w(31, 16, scratch_offset(SLOT_CATCH_INTENT)),
               strb_w(31, 16, scratch_offset(SLOT_EVOLVE_PENDING)),
               strb_w(31, 16, scratch_offset(SLOT_WILD_ENCOUNTER)))
    if _BUDDY_ON:
        cave.bl(game_addr(_HELPER_UE))                # UE: put the partner byte back if we set it
    cave.w(LDP_1617)
    cave.replay_and_return(text, HOOK_E)
    return cave.words()

def cave_buttons_handheld(text, base):  # ZL+RUN catch: mirror the fresh held-buttons u64 into scratch (+0x80)
    # The input pollers are virtual (no static chain to the controller object); mirroring at the
    # poller's own store makes the held state readable anywhere. Write-only to our scratch.
    # The displaced `str x2,[x19,#0x18]` is replayed at the end (nothing here reads [x19+0x18]).
    cave = CaveAsm(base)
    cave.w(STP_1617)
    cave.scratch_page()
    cave.w(str_x(2, 16, scratch_offset(SLOT_BUTTONS)), LDP_1617)
    cave.replay_and_return(text, HOOK_KM)
    return cave.words()

def cave_buttons_shared(text, base):
    # post-poll helper 0x14b8a0. Every style's poller tail-calls it with x0 = the controller object
    # (fresh buttons u64 at [x0+0x18], ZL=bit 8). The helper ignores x0 internally, so reading
    # [x0+0x18] at entry is safe; write-only to our scratch (+0x80). x0 is preserved (only x16/x17
    # touched, saved/restored around the read). Replays the displaced `stp d9,d8,[sp,#-0x50]!` last.
    cave = CaveAsm(base)
    cave.w(STP_1617)
    cave.scratch_page()
    cave.w(ldr_x(17, 0, 0x18),                        # the controller's fresh held buttons
           str_x(17, 16, scratch_offset(SLOT_BUTTONS)), LDP_1617)
    cave.replay_and_return(text, HOOK_KS)
    return cave.words()

ZL_BUTTON_BIT = 8          # nn::hid button bit for ZL
RUN_COMMAND_ID = 4         # the command menu's RUN

def cave_zl_run_decide(text, base):
    # REPLAY-FIRST cave (registry dispfirst=True): the displaced `ldr w20,[x0,#4]` runs first so
    # the cave can test the chosen id in w20 directly (the shape tested live). ZL = nn::hid button
    # bit 8, read from the KM mirror (+0x80).
    cave = CaveAsm(base)
    cave.w(disp(text, HOOK_K), STP_1617)              # ldr w20,[x0,#4] first: w20 = the chosen id
    cave.scratch_page()
    cave.w(ldrb_w(17, 16, scratch_offset(SLOT_WILD_ENCOUNTER)))
    cave.cbz_w(17, 'out')
    cave.w(cmp_i(20, RUN_COMMAND_ID))
    cave.bc(NE, 'out')
    cave.w(ldr_x(17, 16, scratch_offset(SLOT_BUTTONS)),
           str_x(31, 16, scratch_offset(SLOT_BUTTONS)))   # consume on read: a stale mirror (inactive
                                                          # poller, style switch) is read once, then 0,
                                                          # so it can never become a phantom ZL
    cave.tbz(17, ZL_BUTTON_BIT, 'out')
    cave.w(movz_w(17, 1), strb_w(17, 16, scratch_offset(SLOT_CATCH_INTENT)))
    cave.label('out')
    cave.w(LDP_1617)
    cave.b(HOOK_K + 4)
    return cave.words()

def cave_flee_text(text, base):  # ZL+RUN catch polish: skip the message window while a catch is in flight
    cave = CaveAsm(base)
    cave.w(STP_1617)
    cave.scratch_page()
    cave.w(ldrb_w(17, 16, scratch_offset(SLOT_WILD_ENCOUNTER)))
    cave.cbz_w(17, 'original')
    cave.w(ldrb_w(17, 16, scratch_offset(SLOT_CATCH_INTENT)))
    cave.cbz_w(17, 'original')
    cave.w(LDP_1617, RET)                             # a catch is in flight: no window
    cave.label('original')
    cave.w(LDP_1617)
    cave.replay_and_return(text, HOOK_G)
    return cave.words()

# ----- X-CATCH: catch the wild Pokemon with Y from the command menu ----------------------------------
# Register discipline (from the site disassembly):
#  - 0xAC05A0 (WCH) is call setup (`mov x0,x21; bl ...` follows): x0-x15, x30 and NZCV are dead.
#  - 0xAB1FE8 (WCA): the handler tail (str/str/ldr/cbz/blr/epilogue) never reads NZCV; w20 (the
#    chosen id, callee-saved) survives the dispatch's `bl 0x1276d0` into every handler.
# The opponent/partner control (the Y button) emits command-menu event id 5. We redirect the id-5
# jump-table entry to a cave that, in our wild battles and when allowed, commits RUN (action 4) through
# the shared commit tail (cave_after_battle turns that into the native capture); otherwise it runs the
# original id-5 handler.
RODATA_ID5_ENTRY = (0xF9824C, 0xF9823C)   # (entry addr, table base) — id 5 = table[4]
ID5_ORIG_HANDLER = 0xAB1FC8   # original id-5 handler (opens the opponent/partner panel)
CMD_COMMIT_TAIL  = 0xAB1FEC   # shared commit tail: str w8,[x21,#0x10]; str wzr,[x19,#0x184]; exit

def cave_xcatch_eligibility(text, base, anytime=False, press_time=False):  # X-CATCH eligibility (menu build)
    # Runs every menu build for the wild-slot param; writes unlock = eligible (no separate refresh).
    # anytime: unlock=1 whenever it's the wild slot. lowhp: unlock = (4*cur <= max) via one cset
    # (cur>0 is guaranteed at a live command menu, so no separate cur==0 guard). Menus outside our
    # wild battles, and other slots, leave unlock untouched. w9 is dead at 0xAC05A0 (the following bl clobbers it).
    # press_time (the x-lowhp product): do NOT latch eligibility here. Record which object is the wild
    # Pokémon (x21) and let WCA check its HP when Y is pressed. A latch goes stale when the command
    # menu reappears after an attack without being rebuilt: the first Y then opened the vanilla view
    # and only a second Y caught (live-proven 2026-11-04: unlock read 0 at a <=25% menu, 1 after the
    # Y view closed and the menu rebuilt).
    # x21 = this button's battle parameters: slot +0x87, max HP +0x7C, HP +0x7E.
    cave = CaveAsm(base)
    cave.w(STP_1617)
    cave.scratch_base()
    cave.w(ldrb_w(17, 16, SLOT_WILD_ENCOUNTER))
    cave.cbz_w(17, 'out')
    cave.w(ldrb_w(17, 21, 0x87), cmp_i(17, 12))
    cave.bc(NE, 'out')                                # the wild opponent's button
    if press_time:
        cave.w(str_x(21, 16, SLOT_WILD_PARAM))        # WCA reads its HP at the press
    elif anytime:
        cave.w(movz_w(17, 1), strb_w(17, 16, SLOT_XCATCH_UNLOCK))
    else:
        cave.w(ldrh_w(17, 21, 0x7E), ldrh_w(9, 21, 0x7C), lsli_w(17, 17, 2), cmp_w(17, 9),
               cset_ls(17), strb_w(17, 16, SLOT_XCATCH_UNLOCK))   # unlock = 4 * HP <= max HP
    cave.label('out')
    cave.w(LDP_1617)
    cave.replay_and_return(text, HOOK_WCH)            # mov x0,x21
    return cave.words()


def cave_xcatch_action(text, base, press_time=False):  # X-CATCH action: id-5 handler (via redirected jump entry 0xF9824C).
    # Reached only when the player presses the opponent/partner (id-5) control AND the jump table
    # routes id 5 here. In our wild battle, when allowed: arm catch_intent, consume unlock, and commit the **RUN
    # action (w8=4)** through the shared commit tail 0xAB1FEC — exactly as ZL+RUN catch does: RUN
    # yields battle result 3 ("ran away"), which the live-proven cave_after_battle(catch=True) launders into
    # state 3 = the native capture. (Live 2026-11-03: committing action **6** here closed the menu
    # but DEAD-ENDED the battle — no capture, inputs dead — so 6 is not a valid catch action in this
    # flow; action 4 reuses the proven RUN->result3->capture path.) Otherwise fall through to the
    # original id-5 handler (open the panel). x16/x17 free; x19/x21 are the live handler regs.
    # FREEFORM: a jump-table target that tail-branches into the game, not a trampoline to hook+4.
    # press_time: eligibility is decided HERE, at the press, from the wild Pokémon's live HP (WCH only
    # recorded its battle parameters): 4*cur <= max  <=>  cur <= 25%. w9 holds max: x9 is dead on both
    # exits (the original handler 0xAB1FC8 doesn't read it before its bl, the commit tail 0xAB1FEC
    # never reads it, and it is caller-saved), so x16 stays the scratch base.
    cave = CaveAsm(base)
    cave.w(STP_1617)
    cave.scratch_base()
    cave.w(ldrb_w(17, 16, SLOT_WILD_ENCOUNTER))
    cave.cbz_w(17, 'original')
    if press_time:
        cave.w(ldr_x(17, 16, SLOT_WILD_PARAM))
        cave.cbz_x(17, 'original')                    # the wild Pokemon was not recorded
        cave.w(ldrh_w(9, 17, 0x7C), ldrh_w(17, 17, 0x7E), lsli_w(17, 17, 2), cmp_w(17, 9))
        cave.bc(HI, 'original')                       # above 25% HP: the vanilla Y view
    else:
        cave.w(ldrb_w(17, 16, SLOT_XCATCH_UNLOCK))
        cave.cbz_w(17, 'original')
    cave.w(movz_w(17, 1), strb_w(17, 16, SLOT_CATCH_INTENT),   # cave_after_battle -> the capture
           LDP_1617, movz_w(8, RUN_COMMAND_ID))
    cave.b(CMD_COMMIT_TAIL)                           # commit RUN through the shared tail
    cave.label('original')
    cave.w(LDP_1617)
    cave.b(ID5_ORIG_HANDLER)
    return cave.words()

# ===== module registry =====================================================================
# One entry per hook (a hook site + its code cave), in cave-layout order. Bases are assigned
# consecutively from CAVE_BASE in THIS order for ALL modules regardless of variant, so a
# variant's caves keep stable addresses (byte-for-byte reproducible). Each module declares the
# variants that include it and a human note on the scratch slots it touches (the scratch page
# is a shared blackboard, not per-module private, so this is documentation for the manifest).

def cave_battlers_only_exp(text, base, all_battles=False):  # participants-only EXP: zero the per-member Exp.Share input
    cave = CaveAsm(base)
    cave.w(STP_1617)
    cave.scratch_page()
    if not all_battles:
        cave.w(ldrb_w(17, 16, scratch_offset(SLOT_WILD_ENCOUNTER)))
        cave.cbz_w(17, 'out')
    cave.w(movz_w(8, 0))                              # the share byte = 0
    cave.increment(scratch_offset(SLOT_SHARE_ZEROED))
    cave.label('out')
    cave.w(LDP_1617)
    cave.replay_and_return(text, HOOK_X)              # strb w8,[sp,#0x3d]
    return cave.words()

def inplace_return0(text, base):  # in-place body: overwrite a function's first 2 insns with `return 0`
    # `base` == the hook (function entry). Nothing has been pushed yet, so returning here is
    # stack-neutral. The caller of the patched function sees w0 = 0. (Both overwritten words are
    # pinned in PREIMAGE; nothing branches to entry+4 -- checked for XF/XG, 2026-11-04.)
    return [movz_w(0, 0),                         # 0  w0 = 0
            RET]                                  # 1  return to the caller

def inplace_ug(text, base):  # in-place null guard for the helper 0xb78a0 (module UG)
    # Rewrites fn+0xC..+0x1C: drop the unused frame-pointer setup and add `cbz x0` to the helper's own
    # "nothing found" exit, so a NULL object returns an empty handle (the output was already cleared)
    # instead of reading [0+0x78]. Callers passing a real object run exactly as before.
    nothing_found = game_addr(0xB7934)                # the helper's epilogue (ldp x29,x30,[sp,#0x20])
    cave = CaveAsm(base)
    cave.w(mov_x(19, 8), str_x(31, 19, 0))            # mov x19,x8; str xzr,[x19] (output = empty)
    cave.cbz_x(0, nothing_found)                      # NULL object
    cave.w(ldr_x(8, 0, 0x78))
    cave.cbz_x(8, nothing_found)                      # (the original check)
    return cave.words()

def inplace_uh(text, base):   # module UH: 0x1557d0's first instruction -> the null check in its padding
    return [b(base, game_addr(0x155824))]

def inplace_uhp(text, base):  # module UHP: in the padding after 0x1557d0's ret (3 words)
    return [cbz_x(0, base, game_addr(0x155820)),               # 0  NULL object -> ret (do nothing)
            ldr_x(8, 0, 0xF8),                         # 1  the displaced ldr x8,[x0,#0xf8]
            b(base + 8, game_addr(0x1557D4))]                  # 2  back to the function body

def _null_guard(rd, epi_pika, third):
    """in-place: [add x29,..][mov rd,x0][third] -> [mov rd,x0][cbz x0,epilogue][third]"""
    def build(text, base):
        return [mov_x(rd, 0), cbz_x(0, base + 4, game_addr(epi_pika)), third]
    return build

inplace_ui = _null_guard(8,  0x159844, 0xF9400020)   # 3rd word = ldr x0,[x1]          (unchanged)
inplace_uj = _null_guard(20, 0xB6E1C,  0xF9403E88)   # 3rd word = ldr x8,[x20,#0x78]   (unchanged)
inplace_uk = _null_guard(22, 0xB663C,  0xF9403EC8)   # 3rd word = ldr x8,[x22,#0x78]   (unchanged)

def inplace_um(text, base):   # module UM (x0 = the partner model just loaded by ldr x0,[sp,#0x20])
    return [cbz_x(0, base, game_addr(0xA9905C)),               # 0  no partner model -> the step's early return
            movz_w(8, 1),                              # 1  mov w8,#1
            str_x(8, 31, 0x18),                        # 2  [sp+0x18] = 1 (u8 flag) | 0 (u32 at +0x1c)
            add_xi(1, 31, 0x18)]                       # 3  x1 = &that argument (then the original bl)



def module_table(profile, hp_gate='strict', exp_recipients='party', catch_action='off', omit=(),
                 input_source='handheld', x_stage='observe', diagnostics=(),
                 exp_scope='wild', catch_exp='on', options=None):
    # `omit` (default none) drops the named modules from the layout. RESEARCH-ONLY: it exists so a
    # diagnostic build can free cave space by excluding a module not exercised by the test (e.g. N,
    # the wild-KO recall fix, during a capture-EXP probe). Shipping builds NEVER pass omit, so the
    # default path and every byte-identity guarantee are unchanged.
    timer = (profile != 'timed')   # 'classic' (default) removes the 300s wild-battle timer
    catch = (catch_action in ('zl-run', 'x-lowhp', 'x-anytime'))
    strict_gate = (hp_gate == 'strict')
    # scratch_slots = the (offset, length) byte ranges each module owns on the shared scratch page
    # (SCRATCH..SCRATCH+0x100, the last 256 bytes of .bss). verify_scratch_use checks every build: each
    # store lands in a registered byte, and only the SHARED_SCRATCH flags are registered by two modules.
    tbl = [
        dict(key='A',  hook=HOOK_A,  build=lambda t,b: cave_faint_and_timer(t,b,timer=timer,
                                                          timer_gate=bool(options and 'timer' in options['rows'])),
             variants={'addon','main','catch'},
             scratch='instant-end clears +0x34, armed +0xFC; clears the battle timer (evt+0x562)',
             scratch_slots=((SLOT_FAINT_CLEARS, 4), (SLOT_ARMED, 1))),
        dict(key='C',  hook=HOOK_C,  build=cave_joint_guard,  variants={'addon','main','catch'},
             scratch='entries +0xB0, no-joint answers +0xD8; only in our wild battles (+0xF8)',
             scratch_slots=((SLOT_JOINT_ENTRIES, 4), (SLOT_JOINT_GUARD_HITS, 4))),
        dict(key='ST', hook=HOOK_ST, build=lambda t,b: cave_result_rewrite(t,b,strict_gate=strict_gate), variants={'addon','main'},
             scratch='state +0x40, states seen +0x44, battle +0x48, result object +0x50, result +0x58, '
                     'entries +0x5C, KO rewrites +0xE0, armed/done/have/evolve +0xFC..+0xFF',
             scratch_slots=((SLOT_RESULT_STATE, 4), (SLOT_STATES_SEEN, 4), (SLOT_BATTLE, 8), (SLOT_RESULT_OBJECT, 8),
                            (SLOT_RESULT, 4), (SLOT_RESULT_ENTRIES, 4), (SLOT_KO_REWRITES, 4), (SLOT_ARMED, 4))),
        dict(key='R',  hook=HOOK_R,  build=cave_wild_reflect,  variants={'addon','main'},
             scratch='wild HP +0xC8, species +0xCC, max HP +0xD0, slot +0xD4, have +0xFE; only in our wild battles',
             scratch_slots=((SLOT_WILD_HP, 4), (SLOT_WILD_SPECIES, 4), (SLOT_WILD_MAX_HP, 4), (SLOT_WILD_SLOT, 4),
                            (SLOT_HAVE_WILD, 1))),
        dict(key='E',  hook=HOOK_E,  build=lambda t,b: cave_after_battle(t,b,catch=catch), variants={'addon','main','catch'},
             scratch='evolve pending +0xFF; clears the wild-battle flag +0xF8'
                     + ('; reads and clears catch intent +0xDC' if catch else ''),
             scratch_slots=((SLOT_EVOLVE_PENDING, 1),) + (((SLOT_CATCH_INTENT, 4),) if catch else ())),
        dict(key='P',  hook=HOOK_P,  build=cave_exp_gate,  variants={'addon','main','catch'},
             scratch='EXP-gate hits +0xE4; only in our wild battles (+0xF8)',
             scratch_slots=((SLOT_EXP_GATE_HITS, 4),)),
        dict(key='M',  hook=HOOK_M,  build=cave_encounter_marker,  variants={'addon','main','catch'},
             scratch='sets the wild-battle flag +0xF8', scratch_slots=((SLOT_WILD_ENCOUNTER, 4),)),
        dict(key='N',  hook=HOOK_N,  build=cave_no_recall,  variants={'addon','main','catch'},
             data_words=(20 if NORECALL_ENC == 'table' else 10),
             scratch='no-recall runs +0x6C; only in our wild battles (+0xF8)',
             scratch_slots=((SLOT_NO_RECALL_RUNS, 4),)),
    ] + ([
        dict(key='X',  hook=HOOK_X,
             build=lambda t,b: cave_battlers_only_exp(t, b, all_battles=(exp_scope == 'all' or
                                                         bool(options and 'share' in options['rows']))),
             variants={'addon','main','catch'},
             scratch=('Exp. Share bytes zeroed +0x38; '
                      + ('every battle' if exp_scope == 'all' else 'only in our wild battles (+0xF8)')),
             scratch_slots=((SLOT_SHARE_ZEROED, 4),)),
    ] if exp_recipients == 'participants' else []) + ([
        dict(key='KM',
             hook=(HOOK_KM if input_source == 'handheld' else HOOK_KS),
             build=(cave_buttons_handheld if input_source == 'handheld' else cave_buttons_shared),
             variants={'addon','main'},
             scratch='held-buttons mirror +0x80 (u64)'
                     + ('' if input_source == 'handheld' else '; every controller style, via the shared helper 0x14b8a0'),
             scratch_slots=((SLOT_BUTTONS, 8),)),
        dict(key='K',  hook=HOOK_K,  build=cave_zl_run_decide,  variants={'addon','main'}, dispfirst=True,
             scratch='catch intent +0xDC on RUN (id 4) with ZL held; only in our wild battles',
             scratch_slots=((SLOT_CATCH_INTENT, 4),)),
        dict(key='G',  hook=HOOK_G,  build=cave_flee_text,  variants={'addon','main'},
             scratch='no own slots; reads +0xF8/+0xDC (skips the flee-text window mid-catch)',
             scratch_slots=()),
    ] if catch_action == 'zl-run' else [])
    if catch_action in ('x-lowhp', 'x-anytime'):
        anytime = (catch_action == 'x-anytime')
        # The Y catch: two modules, both in the end-of-.text padding. WCH runs at every command-menu
        # button update and records the wild Pokemon (or, for x-anytime and the observe stage, sets the
        # unlock latch). WCA is the id-5 handler: it is freeform (a jump-table target that branches into
        # the game) and no_redir (reached through the rodata id-5 entry, see extra_records), so it has
        # no hook-site branch record. In the x-lowhp divert build (the product) the HP check happens at the
        # Y press (press_time).
        press_time = (not anytime) and x_stage == 'divert'
        tbl += [
            dict(key='WCH', hook=HOOK_WCH,
                 build=lambda t, b_: cave_xcatch_eligibility(t, b_, anytime=anytime, press_time=press_time),
                 variants={'addon', 'main'},
                 scratch=('wild battle parameters +0xB8 (each wild-slot update); reads +0xF8 and the slot +0x87'
                          if press_time else
                          'unlock +0xA0 (set or cleared each wild-slot update); reads +0xF8 and the '
                          'parameters +0x7C/+0x7E/+0x87'),
                 scratch_slots=(((SLOT_WILD_PARAM, 8),) if press_time else ((SLOT_XCATCH_UNLOCK, 1),))),
        ]
        if x_stage == 'divert':
            tbl += [dict(key='WCA', hook=RODATA_ID5_ENTRY[0], no_redir=True, freeform=True,
                         build=lambda t, b_: cave_xcatch_action(t, b_, press_time=press_time),
                         variants={'addon', 'main'},
                         scratch=('sets catch intent +0xDC (with cave_after_battle); reads the wild parameters '
                                  '+0xB8 and checks its HP at the press' if press_time else
                                  'sets catch intent +0xDC (with cave_after_battle); reads the unlock +0xA0'),
                         scratch_slots=()),
                    # module G (shared with ZL+RUN catch): skip the "Got away safely!" message window
                    # while a catch is in flight (our wild battle and catch intent set): WCA sets the
                    # intent, so G hides the RUN flee text before the native capture scene.
                    dict(key='G', hook=HOOK_G, build=cave_flee_text, variants={'addon', 'main'},
                         scratch='no own slots; reads +0xF8/+0xDC (skips the flee-text window mid-catch)',
                         scratch_slots=())]
    tbl += list(diagnostics)                     # extra modules of a test build (none in a release)
    if catch_exp == 'off':   # catches give no EXP + no EXP screen (CLI/BuildSpec default 'off')
        # No catch EXP. Laid out last so every earlier cave keeps its address.
        tbl += [dict(key='XF', hook=HOOK_XF, build=inplace_return0, variants={'addon','main','catch'},
                     inplace=True, freeform=True,   # overwrite the fn entry; no cave, no redirect
                     scratch='none; catch-EXP formula 0x82d3f0 returns 0 (no catch EXP)',
                     scratch_slots=()),
                dict(key='XG', hook=HOOK_XG, build=inplace_return0, variants={'addon','main','catch'},
                     inplace=True, freeform=True,
                     scratch='none; catch-EXP gate 0x830410 returns false (no EXP screen after a catch)',
                     scratch_slots=())]
    tbl += [dict(key='UG', hook=HOOK_UG, build=inplace_ug, variants={'addon','main','catch'},
                 inplace=True, freeform=True,           # rewrite 5 words in place; no cave, no redirect
                 scratch='none; NULL-safe resource helper 0xb78a0 (wild battles before the partner unlock)',
                 scratch_slots=()),
            dict(key='UH', hook=HOOK_UH, build=inplace_uh, variants={'addon','main','catch'},
                 inplace=True, freeform=True,
                 scratch='none; 0x1557d0 entry -> its NULL check in its own padding (UHP)', scratch_slots=()),
            dict(key='UHP', hook=HOOK_UHP, build=inplace_uhp, variants={'addon','main','catch'},
                 inplace=True, freeform=True,
                 scratch='none; NULL check in the 3 padding words after 0x1557d0', scratch_slots=())]
    tbl += [dict(key=k, hook=h, build=f, variants={'addon','main','catch'}, inplace=True, freeform=True,
                 scratch='none; NULL-safe helper %#x (pre-unlock battle intro)' % h, scratch_slots=())
            for k, h, f in (('UI', HOOK_UI, inplace_ui), ('UJ', HOOK_UJ, inplace_uj), ('UK', HOOK_UK, inplace_uk))]
    tbl += buddy_modules()
    if _BUDDY_ON:
        tbl += [dict(key='UE', hook=game_addr(_HELPER_UE), build=ue_helper, variants={'addon','main','catch'}, inplace=True,
                     freeform=True, scratch='partner-enabled force flag +0xF0 (UE)', scratch_slots=((SLOT_PARTNER_FORCED, 1),),
                     adrp_ok=(0x15FA000,))]                # the game's singleton page (as the built-in base reads it)
    tbl += [dict(key='UM', hook=HOOK_UM, build=inplace_um, variants={'addon','main','catch'}, inplace=True,
                 freeform=True, scratch='none; NULL partner model -> early return of 0xa98b40', scratch_slots=())]
    if omit:
        assert 'M' not in omit, "module M (encounter marker) is load-bearing; never omit"
        tbl = [m for m in tbl if m['key'] not in omit]
    if _bo.active(options):
        keys = {m['key'] for m in tbl}
        if 'catch_exp' in options['rows']:          # XF/XG: entry -> gate, 2nd word restored
            for m in tbl:
                if m['key'] in ('XF', 'XG'):
                    m['build'] = _bo.inplace_gate('OXFG' if m['key'] == 'XF' else 'OXGG')
        tbl += _bo.modules(options, keys)
    return tbl

_FULL_ORDER = dict(
    redir=['A', 'C', 'ST', 'P', 'R', 'E', 'N', 'X', 'KM', 'K', 'G', 'WCH', 'XF', 'XG'],
    body=['A', 'C', 'ST', 'R', 'E', 'P', 'M', 'N', 'X', 'KM', 'K', 'G', 'WCH', 'WCA', 'XF', 'XG',
          'UG', 'UH', 'UHP', 'UI', 'UJ', 'UK', 'UM', 'UE'] + BUDDY_KEYS)
VARIANT_ORDER = {
    'addon': _FULL_ORDER,
    'main': _FULL_ORDER,
    'catch': dict(redir=['A', 'C', 'P', 'E', 'N', 'X', 'XF', 'XG'],
                  body=['A', 'C', 'P', 'E', 'M', 'N', 'X', 'XF', 'XG', 'UG', 'UH', 'UHP', 'UI', 'UJ', 'UK', 'UM', 'UE']
                  + BUDDY_KEYS),
}

def lay_out(text, profile, hp_gate='strict', exp_recipients='party', catch_action='off', omit=(),
            input_source='handheld', x_stage='observe', diagnostics=(), strict=True,
            exp_scope='wild', catch_exp='on', options=None):
    global _BUDDY_ON, OPTIONS
    _BUDDY_ON = BUDDY_FORCE and 'UE' not in omit
    OPTIONS = (_bo.normalize(options, catch_action, exp_recipients, catch_exp, profile, exp_scope, 'N' not in omit)
               if options else None)
    _bo.LAYOUT_TEXT[0] = text
    _bo.ADDR.clear()
    _INTENTIONAL.clear()
    pc_lo, pc_hi = game_addr(_bo.PCAVE[0]), game_addr(_bo.PCAVE[1] - 4) + 4
    p2_lo, p2_hi = game_addr(_bo.PCAVE2[0]), game_addr(_bo.PCAVE2[1] - 4) + 4
    for _pass in ((0, 1) if _bo.active(OPTIONS) else (1,)):   # the options layer cross-references caves:
        caves, base, pbase, p2base = {}, CAVE_BASE, pc_lo, p2_lo   # pass 0 finds the bases
        for m in module_table(profile, hp_gate, exp_recipients, catch_action, omit, input_source, x_stage,
                              diagnostics, exp_scope, catch_exp, OPTIONS):
            if m.get('inplace'):   # overwrites the hook site itself: no cave space, no redirect
                caves[m['key']] = dict(m, base=m['hook'], words=m['build'](text, m['hook']))
                continue
            region = m.get('region')
            assert region in (None, 'pcave', 'pcave2'), ("unknown cave region", m['key'], region)
            cur = {'pcave': pbase, 'pcave2': p2base}.get(region, base)
            if region in ('pcave', 'pcave2') and cur % 8:
                cur += 4                                       # 8-byte align (data tables, u64 hashes)
            w = m['build'](text, cur)
            caves[m['key']] = dict(m, base=cur, words=w)
            if region == 'pcave':
                pbase = cur + len(w) * 4
            elif region == 'pcave2':
                p2base = cur + len(w) * 4
            else:
                base = cur + len(w) * 4
        if _pass == 0:
            _bo.ADDR.update({k: c['base'] for k, c in caves.items()})
    if 'X' in caves and 'OXG' in caves:
        caves['X']['redir_to'] = caves['OXG']['base']
    if 'N' in caves and 'ONG' in caves:
        caves['N']['redir_to'] = caves['ONG']['base']
    if 'A' in caves and 'OTM' in caves:
        caves['A']['detour'] = {TIMER_WORD_INDEX}      # its timer word: b OTM, which returns to the next word
    if strict:   # strict=False: a dry-run layout for fit reporting (mod_spec.layout_fit) — the
                 # caller inspects the returned bases/end instead of crashing on overflow.
        assert base <= CAVE_END_PAGE, ("caves overflow the .text padding", hex(base))
        assert pbase <= pc_hi, ("options caves overflow the reclaimed protobuf region", hex(pbase), hex(pc_hi))
        assert p2base <= p2_hi, ("options caves overflow the second reclaimed region", hex(p2base), hex(p2_hi))
        if _bo.active(OPTIONS):
            _bo.verify_pcave(text, caves)
            _bo.verify_options(text, caves)
    return caves


def used_scratch(caves):
    """Set of scratch byte offsets OWNED by the modules present in `caves` (a lay_out() result).
    Basis for the diagnostic-overlap guard: a research probe's ring must be disjoint from this."""
    used = set()
    for c in caves.values():
        for off, length in c.get('scratch_slots', ()):
            used.update(range(off, off + length))
    return used

SHARED_SCRATCH = set(range(0xDC, 0xE0)) | set(range(0xF8, 0x100))   # flags several modules set and clear
_SCRATCH_ACCESS_SIZE = {'strb': 1, 'sturb': 1, 'strh': 2, 'sturh': 2}

def _scratch_group(key):
    """Modules that share their slots on purpose count as one owner: the options layer is one."""
    return 'options' if key in _bo.OPT_KEYS else key

def scratch_writes(cave):
    """(offset, length) of every store a cave makes to the scratch page, decoded from its words. A
    register counts as pointing at the page after `adrp` to it (plus any `add #imm`), until it is
    written again or the code returns."""
    page = SCRATCH & ~0xFFF
    words = cave['words'][:len(cave['words']) - cave.get('data_words', 0)]
    code = b''.join(struct.pack('<I', w) for w in words)
    offset_of = {}                       # register name -> its value minus the scratch page
    found = set()
    for ins in MD_DETAIL.disasm(code, cave['base']):
        ops = ins.operands
        if ins.mnemonic == 'adrp' and ops[1].imm == page:
            offset_of[ins.reg_name(ops[0].reg)] = 0
            continue
        if (ins.mnemonic == 'add' and len(ops) == 3 and ops[2].type == ARM64_OP_IMM
                and ins.reg_name(ops[1].reg) in offset_of):
            offset_of[ins.reg_name(ops[0].reg)] = offset_of[ins.reg_name(ops[1].reg)] + ops[2].imm
            continue
        if ins.mnemonic.startswith('st'):
            for op in ops:
                if op.type == ARM64_OP_MEM and op.mem.base and ins.reg_name(op.mem.base) in offset_of:
                    size = _SCRATCH_ACCESS_SIZE.get(ins.mnemonic, 8 if ins.reg_name(ops[0].reg)[0] == 'x' else 4)
                    if ins.mnemonic == 'stp':
                        size *= 2
                    found.add((offset_of[ins.reg_name(op.mem.base)] + op.mem.disp - (SCRATCH - page), size))
        _, written = ins.regs_access()
        for reg in written:
            name = ins.reg_name(reg)
            offset_of.pop('x' + name[1:] if name[0] == 'w' else name, None)
        if ins.mnemonic in ('bl', 'blr'):
            for i in range(18):
                offset_of.pop('x%d' % i, None)
        if ins.mnemonic == 'ret':
            offset_of.clear()
    return found

def verify_scratch_use(caves):
    """Every scratch store lands in a registered slot, and no two modules register the same byte
    unless it is one of the SHARED_SCRATCH flags. This is what keeps a new module from reusing a
    byte another module already writes."""
    owners = {}
    for key, cave in caves.items():
        for off, length in cave.get('scratch_slots', ()):
            for byte in range(off, off + length):
                owners.setdefault(byte, set()).add(_scratch_group(key))
    clashes = sorted(byte for byte, groups in owners.items() if len(groups) > 1 and byte not in SHARED_SCRATCH)
    assert not clashes, ("scratch bytes registered by more than one module",
                         {hex(byte): sorted(owners[byte]) for byte in clashes})
    for key, cave in caves.items():
        if cave.get('data'):
            continue
        for off, length in scratch_writes(cave):
            unregistered = [byte for byte in range(off, off + length) if byte not in owners]
            assert not unregistered, ("module writes scratch bytes no module registers", key, hex(off), length)

def verify_caves(text, caves):
    """capstone self-checks per cave: it returns to hook+4, preserves the displaced original,
    and is stack-balanced on every path (dataflow depth)."""
    def dis(words, base): return list(MD.disasm(b''.join(struct.pack('<I', x) for x in words), base))
    for key, c in caves.items():
        if c.get('data'):                  # a data block (options strings/tables): never executed
            continue
        w, base, ret = c['words'], c['base'], c['hook'] + 4
        nd = c.get('data_words', 0)        # trailing non-executed data words (e.g. poke tables)
        if nd:
            w = w[:-nd]
        ins = dis(w, base)
        assert len(ins) == len(w), (key, 'undecodable instruction at word', len(ins))
        if c.get('relocated'):             # a relocated copy of a game function (checked by verify_options)
            continue
        d0 = c.get('entry_depth', 0)       # >0: the cave starts inside the hooked function's own frame
        free = c.get('freeform')           # non-standard shape (in place, a jump-table target): no ret-site/displaced
                                           # convention; stack balance + exits still verified below
        if not free:
            assert ins[-1].mnemonic == 'b' and int(ins[-1].op_str[1:], 16) == ret, (key, 'ret', ins[-1].op_str)
        di = None if free else (0 if c.get('dispfirst') else len(w) - 2)
        if di is not None:
            assert struct.pack('<I', w[di]) == struct.pack('<I', disp(text, c['hook'])), (key, 'displaced')
        for i in ins:                      # relocation proof: every emitted adrp must reach the
            if i.mnemonic == 'adrp':       # scratch page regardless of which cave region it's in
                assert int(i.op_str.split('#')[-1], 16) in {SCRATCH_PAGE_ADDRESS} | set(c.get('adrp_ok', ())), (key, 'adrp target', i.op_str)
        idx = {i.address: k for k, i in enumerate(ins)}
        def delta(i):
            if di is not None and idx[i.address] == di:
                return 0                    # the replayed displaced insn belongs to the
                                            # TARGET fn's frame (e.g. its prologue push)
            if i.mnemonic == 'stp' and '#-' in i.op_str and i.op_str.endswith('!'):
                return 1
            if i.mnemonic == 'ldp' and '], #' in i.op_str:
                return -1
            return 0
        depth = {ins[0].address: d0}
        work = [ins[0].address]
        lo, hi = base, base + len(w) * 4

        def reach(target, d_out):
            """`target` is entered with stack depth d_out; every way in must agree."""
            if target in depth:
                assert depth[target] == d_out, (key, 'depth conflict', hex(target), depth[target], d_out)
            else:
                depth[target] = d_out
                work.append(target)

        while work:
            addr = work.pop()
            i = ins[idx[addr]]
            d_out = depth[addr] + delta(i)
            succs = []
            if idx[addr] in c.get('detour', ()):          # a b to a gate that returns to the next word
                reach(ins[idx[addr] + 1].address, d_out)
                continue
            is_uncond_b = (i.mnemonic == 'b')
            is_branch = i.mnemonic in ('cbz', 'cbnz', 'tbz', 'tbnz') or i.mnemonic.startswith('b.') or is_uncond_b
            if i.mnemonic == 'ret':
                assert depth[addr] == 0, (key, 'ret unbalanced', hex(addr), depth[addr])
                continue
            if is_branch:
                tgt = int(i.op_str.split('#')[-1], 16)
                if lo <= tgt < hi:
                    succs.append(tgt)
                else:
                    assert d_out == d0, (key, 'cave exit unbalanced', hex(addr), d_out)
            if not is_uncond_b and idx[addr] + 1 < len(ins):
                succs.append(ins[idx[addr] + 1].address)
            for t in succs:
                reach(t, d_out)

def verify_preimages(text):
    """Every hook site in text.bin holds the exact instruction we expect to displace."""
    for mem, expect in PREIMAGE.items():
        got = struct.unpack_from('<I', text, mem)[0]
        assert got == expect, ("preimage mismatch (wrong NSO build?)", hex(mem),
                               "got 0x%08X want 0x%08X" % (got, expect))

def verify_cave_padding(text, caves):
    """The portion of the TAIL cave region present in text.bin is zero padding (no live code).
    (Bytes past len(text) are loader-provided zero page padding, not carved into the file.)"""
    tail = [c for c in caves.values() if c.get('region') is None and not c.get('inplace')]
    cave_end = max(c['base'] + len(c['words']) * 4 for c in tail)
    assert cave_end <= CAVE_END_PAGE, ("caves overflow .text padding", hex(cave_end))
    assert len(text) >= CAVE_BASE, ("text.bin shorter than cave base", hex(len(text)))
    seg = text[CAVE_BASE:min(cave_end, len(text))]
    nz = next((CAVE_BASE + i for i, v in enumerate(seg) if v), None)
    assert nz is None, ("cave region is not zero padding at", hex(nz) if nz else None)


def extra_records(caves):
    """X-CATCH records that are not hook-redirect+cave pairs (id-5 design): just the id-5 jump-table
    entry redirect (divert only) → WCA. No classifier, no WCV visibility, no setup NOPs — the id-5
    control is natively pressable and does not divert, so nothing else is needed."""
    recs = []
    if 'WCA' in caves:                           # only the divert stage emits WCA
        tgt = caves['OQG']['base'] if 'OQG' in caves else caves['WCA']['base']   # ZL+RUN catch row gate
        recs.append((RODATA_ID5_ENTRY[0], struct.pack('<i', tgt - RODATA_ID5_ENTRY[1])))
    if any(k in caves for k in _bo.OPT_KEYS):    # options layer: rodata entries + base-record overrides
        more, ov = _bo.extra_records(caves)
        _INTENTIONAL.update(ov)
        recs += more
    return recs

def verify_marker(merged):
    """The intentional overlap is only safe if the base's own record writes the marker store at
    HOOK_M. Find the base record covering HOOK_M (not our 4-byte redirect) and assert it."""
    for mem, data in merged:
        if mem != HOOK_M and mem <= HOOK_M < mem + len(data):
            off = HOOK_M - mem
            if off + 4 <= len(data):
                word = struct.unpack_from('<I', data, off)[0]
                assert word == MARKER_WORD, (
                    "the base record does not write the marker at HOOK_M", "0x%08X" % word)
                return
    raise SystemExit("base marker record not found covering HOOK_M (wrong external base file?)")

def variant_records(variant, caves):
    """(mem, bytes) records for a variant, in VARIANT_ORDER.
    no_redir modules are reached via a rodata jump-table entry (extra_records) instead of a
    hook-site branch; redir_bl modules get a `bl` redirect (the site's own call is the hook)."""
    order = VARIANT_ORDER[variant]
    recs = [(caves[k]['hook'],
             struct.pack('<I', (bl_ if caves[k].get('redir_bl') else b)(caves[k]['hook'],
                                                                      caves[k].get('redir_to', caves[k]['base']))))
            for k in order['redir'] if k in caves and not caves[k].get('no_redir')
            and not caves[k].get('inplace')]
    recs += [(caves[k]['base'], b''.join(struct.pack('<I', x) for x in caves[k]['words']))
             for k in order['body'] if k in caves]
    opt = [k for k in _bo.OPT_KEYS if k in caves]          # the options layer (after everything else)
    recs += [(caves[k]['hook'], struct.pack('<I', (bl_ if caves[k]['redir'] == 'bl' else b)(caves[k]['hook'], caves[k]['base'])))
             for k in opt if caves[k]['redir']]
    recs += [(caves[k]['base'], b''.join(struct.pack('<I', x) for x in caves[k]['words'])) for k in opt]
    m = caves['M']
    recs.append((m['hook'], struct.pack('<I', b(m['hook'], m['base']))))  # M redirect applied last
    return recs

def load_base_src(path):
    out = []
    for ln in Path(path).read_text().splitlines():
        ln = ln.strip()
        if ln and not ln.startswith('@') and not ln.startswith('//'):
            addr, hx = ln.split(None, 1)
            out.append((int(addr, 16) - PCHTXT_OFF, bytes.fromhex(hx.strip())))  # store as mem offset
    return out

_INTENTIONAL = set()        # base-record addresses the options layer overrides (extra_records)

def overlap_check(label, merged):
    """Pairwise record overlap (in pchtxt space). The one intentional overlap is HOOK_M's
    redirect sitting inside the base's 32-byte record at 0x3059B0 (pchtxt 0x305AB0); everything else must be disjoint."""
    rngs = [(m + PCHTXT_OFF, m + PCHTXT_OFF + len(d)) for m, d in merged]
    intentional = {HOOK_M + PCHTXT_OFF} | {a + PCHTXT_OFF for a in _INTENTIONAL}
    for i in range(len(rngs)):
        for j in range(i + 1, len(rngs)):
            if rngs[i][0] < rngs[j][1] and rngs[j][0] < rngs[i][1]:
                if max(rngs[i][0], rngs[j][0]) in intentional:
                    continue
                raise SystemExit("OVERLAP (%s) records %d/%d" % (label, i, j))

def disjoint(records):
    """Fold every overlapping record into the earlier record that covers it, as the final memory
    image would look in file order, so no two records overlap. Eden keeps a mod's records in an
    unordered_map (src/core/file_sys/ips_layer.cpp) and applies them in hash order, NOT file order:
    with an overlap, which record wins depends on the addresses and the platform's hash table. (The
    HOOK_M redirect inside the base's marker record won on macOS Eden for Pikachu and lost for Eevee,
    found live 2026-11-05.) Disjoint records make the result independent of application order."""
    out = [(m, bytearray(d)) for m, d in records]
    keep = [True] * len(out)
    for j, (mj, dj) in enumerate(out):
        for i in range(j):
            mi, di = out[i]
            if keep[i] and mi <= mj and mj + len(dj) <= mi + len(di):     # j lies inside an earlier record
                di[mj - mi: mj - mi + len(dj)] = dj
                keep[j] = False
                break
    res = [(m, bytes(d)) for (m, d), k in zip(out, keep) if k]
    rngs = sorted((m, m + len(d)) for m, d in res)
    for (a0, a1), (b0, b1) in zip(rngs, rngs[1:]):
        assert a1 <= b0, ("records still overlap after disjoint() (partial overlap?)", hex(a0), hex(b0))
    return res

def emit_ips32(records, path):
    """The same records as an IPS32 patch for Atmosphere (atmosphere/exefs_patches/<mod>/<build-id>.ips).
    Offsets use the .pchtxt convention (memory offset + 0x100, i.e. the NSO image with its header). Records
    are disjoint already; long ones are split at 0xFFFF bytes; no record may start at "EEOF"."""
    out = bytearray(b'IPS32')
    for mem, data in sorted(records):
        off = mem + PCHTXT_OFF
        for i in range(0, len(data), 0xFFFF):
            chunk = data[i:i + 0xFFFF]
            assert off + i != 0x45454F46 and chunk, ("IPS32: unusable record", hex(off + i))
            out += struct.pack('>IH', off + i, len(chunk)) + chunk
    out += b'EEOF'
    Path(path).write_bytes(bytes(out))
    return bytes(out)

def ips32_name():
    return NSOBID[:32].upper() + '.ips'

def emit(records, path, header_comment):
    lines = [f"@nsobid-{NSOBID}", ""] + header_comment + ["@enabled"]
    for mem, data in records:
        lines.append("%08X %s" % (mem + PCHTXT_OFF, data.hex().upper()))
    lines.append("@stop")
    Path(path).write_text("\n".join(lines) + "\n")

IPS32_RELEASE_PATH = "atmosphere/exefs_patches/Wild Battles Remastered/<build-id>.ips (in the Atmosphere zip)"

def write_manifest(path, profile, caves, outputs, hp_gate='slot6', exp_recipients='party', catch_action='off',
                   exp_scope='wild', catch_exp='on', base='external'):
    import json, hashlib
    tail = {k: c for k, c in caves.items() if c.get('region') is None}
    man = dict(
        nsobid=NSOBID, base=base, profile=profile, hp_gate=hp_gate, exp_recipients=exp_recipients,
        battle_exp_scope=exp_scope,
        catch_exp=catch_exp,
        catch_action=catch_action,
        cave_base=hex(CAVE_BASE),
        cave_end=hex(max(c['base'] + len(c['words']) * 4 for c in tail.values())),
        modules={k: dict(hook=hex(c['hook']), cave=hex(c['base']), words=len(c['words']),
                         variants=sorted(c['variants']), scratch=c['scratch'],
                         **({'inplace': True} if c.get('inplace') else {}))
                 for k, c in caves.items()},
        outputs={})
    if OPTIONS and _bo.active(OPTIONS):
        man['options'] = dict(menu_rows=list(OPTIONS['row_keys']),
                              trade_evolution=OPTIONS['trade'], scripted_vanilla=bool(OPTIONS['scripted_vanilla']),
                              caught_marker=bool(OPTIONS['caught_marker']),
                              cfg_bits={k: [_bo.CFG_SHIFT + _bo.ROW[k]['lsb'], _bo.ROW[k]['width']]
                                        for k in OPTIONS['row_keys']},
                              defaults={k: OPTIONS['default'][k] for k in OPTIONS['row_keys']},
                              region=[hex(game_addr(_bo.PCAVE[0])), hex(game_addr(_bo.PCAVE[1] - 4) + 4)],
                              region_sha256=_bo.PCAVE_SHA256[GAME])
    for name, (p, recs) in outputs.items():
        recorded_path = IPS32_RELEASE_PATH if name == 'ips32' else p      # the IPS32 file is built to a temp path
        man['outputs'][name] = dict(path=recorded_path, records=len(recs),
                                    sha256=hashlib.sha256(Path(p).read_bytes()).hexdigest())
    Path(path).write_text(json.dumps(man, indent=2) + "\n")

# The built-in base's routine replaces the game's function B at ROUTINE_ENTRY. (ROUTINE_* addresses
# are re-pointed by set_game like HOOK_*, so they are used as they are, not through game_addr().) Two of its words are
# anchors other code depends on: word 18 is the marker store module M hooks (HOOK_M = 0x3059B8), and
# word 65 is the fallback the scripted-fight record at 0x28BF1C (and the options gate OEF) branch to.
ROUTINE_ENTRY = 0x305970
ROUTINE_COUNT_CAVE = 0x305B10        # the routine's count cave (the 0x28CB10 record branches here)
ROUTINE_MARKER_INDEX = 18
ROUTINE_FALLBACK_INDEX = 65
ROUTINE_CODE_WORDS = 71              # then the 8-word participant table (data)
ROUTINE_TABLE_WORDS = 8
PARTICIPANT_INITS = ((0x28DBC0, 0), (0x28DBE0, 0), (0x28DC10, 1), (0x28DBF0, 1))   # (init fn, w1)
SINGLETON_A_OFF = 0xAB8              # on _bo.SINGLETON_PAGE, beside the singleton holder B (+0xD08)

def base_routine():
    """The built-in base's routine: words and labels (a CaveAsm at ROUTINE_ENTRY).

    x21 = the output (x8), x20 = the battle object (x0), x2 = the encounter descriptor."""
    cave = CaveAsm(ROUTINE_ENTRY)
    cave.w(stp_pre(29, 30, 31, -0x50), stp_off(20, 21, 31, 0x10), add_xi(29, 31, 0),
           mov_x(21, 8), mov_x(20, 0))
    for off in range(0, 0x60, 0x10):                 # copy the descriptor to battle+0x58, unrolled so
        cave.w(ldp_off(5, 6, 2, off), stp_off(5, 6, 20, 0x58 + off))   # the marker lands on word 18
    cave.w(movz_w(9, 1))
    cave.label('marker')
    cave.w(str_w(9, 20, 0x298))                      # the marker (= MARKER_WORD, at HOOK_M)
    # singleton B first, then A, and the struct stores + construct args reordered (all
    # independent) — identical effect, but breaks the ABI-forced byte sequence's overlap.
    cave.adrp(12, _bo.SINGLETON_PAGE)
    cave.w(ldr_x(12, 12, _bo.SINGLETON_OFF), ldr_x(12, 12, 0), ldr_x(12, 12, 0x68))   # singleton B (x12)
    cave.adrp(11, _bo.SINGLETON_PAGE)
    cave.w(ldr_x(11, 11, SINGLETON_A_OFF), ldr_x(11, 11, 0), ldr_x(11, 11, 0x68))      # singleton A (x11)
    cave.cbz_x(12, 'fallback')
    cave.w(str_x(31, 31, 0x28), str_x(20, 31, 0x38), str_w(31, 31, 0x30), str_x(12, 31, 0x20),
           mov_x(1, 11), add_xi(2, 31, 0x30), add_xi(8, 31, 0x28), add_xi(0, 31, 0x20))
    cave.bl(game_addr(0x28BC40))                     # construct the battle
    cave.w(ldr_x(0, 31, 0x28))
    cave.cbz_x(0, 'fallback')
    # One loop over the participant table instead of four unrolled init blocks. x15 = the routine's
    # address (an init's entry = x15 + its delta), x13 = the table, x14 = the count; w2 is always 1.
    cave.adr(15, cave.base)
    cave.adr(13, 'participants')
    cave.w(movz_w(14, len(PARTICIPANT_INITS)))
    cave.label('next')
    cave.w(add_xi(8, 31, 0x40), add_xi(0, 31, 0x28))
    cave.bl(game_addr(0x17EEE0))                     # the next participant
    cave.w(ldr_x(0, 31, 0x40))
    cave.cbz_x(0, 'no_init')
    cave.w(ldr_w(1, 13, 0), movz_w(2, 1), ldrsw_i(16, 13, 4), add_x_reg(16, 15, 16), blr_(16))   # init(w1, 1)
    cave.label('no_init')
    cave.w(ldr_x(0, 31, 0x40))
    cave.cbz_x(0, 'advance')
    cave.w(ldr_x_pre(8, 0, 0x48), ldr_x(8, 8, 0x18), blr_(8), str_x(31, 31, 0x40))   # its virtual finalize
    cave.label('advance')
    cave.w(add_xi(13, 13, 8), subs_wi(14, 14, 1))
    cave.bc(NE, 'next')
    cave.w(str_x(31, 21, 0),                         # str xzr,[x21]
           ldp_off(20, 21, 31, 0x10), ldp_post(29, 30, 31, 0x50), RET)
    cave.label('fallback')
    cave.adrp(1, _bo.SINGLETON_PAGE)
    cave.w(ldr_x(1, 1, _bo.SINGLETON_OFF), ldr_x(1, 1, 0))
    cave.bl(game_addr(0x28D830))
    cave.cbz_x(20, game_addr(0x28C1AC))
    cave.b(game_addr(0x28BF20))
    cave.label('participants')                       # [w1: u32, entry - routine: s32] per init
    for entry, w1 in PARTICIPANT_INITS:
        cave.w(w1 & 0xFFFFFFFF, (game_addr(entry) - cave.base) & 0xFFFFFFFF)
    assert cave.labels['marker'] == ROUTINE_MARKER_INDEX and cave.address_of('marker') == HOOK_M
    assert cave.labels['fallback'] == ROUTINE_FALLBACK_INDEX
    assert cave.labels['participants'] == ROUTINE_CODE_WORDS
    assert len(cave.items) == ROUTINE_CODE_WORDS + ROUTINE_TABLE_WORDS
    return cave

def routine_fallback():
    return ROUTINE_ENTRY + 4 * ROUTINE_FALLBACK_INDEX

def base_self_records(text):
    """Records: the routine (see base_routine), its count cave, and the game edits that route a wild
    encounter into it.
    """
    def packed(words):
        return b''.join(struct.pack('<I', w) for w in words)
    recs = [
        (game_addr(0x28BE84), packed([movz_w(8, 9), NOP, b(game_addr(0x28BE8C), game_addr(0x28BE94))])),  # count = 9, skip the increment
        (game_addr(0x28BF1C), packed([b(game_addr(0x28BF1C), routine_fallback())])),   # -> the routine's fallback
        (game_addr(0x28C1A0), packed([movz_w(3, 0xA)])),                                # param 2 -> 10
        (game_addr(0x28CB10), packed([b(game_addr(0x28CB10), ROUTINE_COUNT_CAVE)])),
        (game_addr(0x2FDF34), packed([NOP])),                                           # the game's calls to fn B: off
        (game_addr(0x2FDF80), packed([NOP])),
        (game_addr(0x3057B8), packed([ldr_x(20, 8, 0x68)])),                            # repoint the field load
        (game_addr(0x305934), packed([bl_(game_addr(0x305934), ROUTINE_ENTRY)])),   # call our routine
        (game_addr(0x319FF4), packed([movz_w(24, 1)])),                                 # flag = 1
    ]
    # The routine goes out as 0x20-byte records (8 words each; this is the pinned record layout).
    # Module M's 4-byte record at HOOK_M overlaps one of them: disjoint() folds it into that record,
    # because Eden applies a mod's records in no fixed order.
    body = packed(base_routine().words())
    for off in range(0, len(body), 0x20):
        recs.append((ROUTINE_ENTRY + off, body[off:off + 0x20]))
    recs.append((ROUTINE_COUNT_CAVE,
                 packed([ldrb_w(8, 19, 0x735), cmp_i(8, 0), movz_w(2, 0xA), movz_w(8, 9), csel_w(2, 8, 2, NE),
                         b(ROUTINE_COUNT_CAVE + 0x14, game_addr(0x28CB14))])))   # the count cave
    return recs

# Header for the self-base unified mod. It is the PUBLIC release payload, byte-for-byte (the public
# exporter copies this file as-is), so it must stay free of private/prior-build wording. ASCII only.
SELF_UNIFIED_HEADER = [
    "// Wild Battles Remastered - turn-based wild battles with native EXP for",
    "// Pokemon: Let's Go, Pikachu! 1.0.2. One self-contained mod; load it alone.",
    "// pchtxt addr = memory offset + 0x100.",
]
EXTERNAL_UNIFIED_HEADER = [
    "// Wild Battles Remastered (UNIFIED) - an external base + this mod's modules in ONE file.",
    "// Load this alone; do NOT also enable the external base mod separately.",
    "// pchtxt addr = memory offset + 0x100.",
]

def unified_base(text, base, base_src_path=None):
    if base == 'self':
        recs = base_self_records(text)
        verify_base_self(text, recs)
        hdr = [line.replace("Let's Go, Pikachu!", GAME_INFO[GAME]['title']) for line in SELF_UNIFIED_HEADER]
        return recs, hdr
    assert base == 'external' and base_src_path, ("unified_base: base must be 'self', or 'external' with a base_src_path", base)
    return load_base_src(base_src_path), EXTERNAL_UNIFIED_HEADER

def verify_base_self(text, recs):
    """Self-verify the built-in base: build-id preimages, every emitted CODE word decodes (the
    routine's participant table is DATA and excluded), and the game-dictated anchors (the marker at
    HOOK_M, the fallback, the count cave) hold."""
    for mem, word in BASE_PREIMAGE.items():
        got, = struct.unpack_from('<I', text, mem)
        assert got == word, ("base preimage mismatch (wrong NSO build?)", hex(mem), hex(got), hex(word))
    by_address = dict(recs)
    routine = bytearray()
    addr = ROUTINE_ENTRY                  # reassemble the routine from its 0x20-byte records
    chunk_addrs = []
    while addr in by_address:
        chunk_addrs.append(addr)
        routine += by_address[addr]
        addr += len(by_address[addr])
    assert len(routine) == (ROUTINE_CODE_WORDS + ROUTINE_TABLE_WORDS) * 4, ("routine length", len(routine))
    code = bytes(routine[:ROUTINE_CODE_WORDS * 4])
    decoded = sum(4 for _ in MD.disasm(code, ROUTINE_ENTRY))
    assert decoded == len(code), ("undecodable word in routine code", decoded, "of", len(code))
    chunks = set(chunk_addrs)
    for mem, data in recs:                           # the other records (hooks + count cave) are all code
        if mem in chunks:
            continue
        decoded = sum(4 for _ in MD.disasm(data, mem))
        assert decoded == len(data), ("undecodable word in base record at", hex(mem), "ok", decoded, "of", len(data))
    fallback, = struct.unpack_from('<I', routine, ROUTINE_FALLBACK_INDEX * 4)
    assert (fallback & 0x9F000000) == 0x90000000, "the routine's fallback does not start with its adrp"
    assert ROUTINE_COUNT_CAVE in by_address, "count cave missing"
    assert struct.unpack_from('<I', routine, ROUTINE_MARKER_INDEX * 4)[0] == MARKER_WORD, "marker word drifted"

def cli_options(a):
    """The options-layer dict from the CLI flags (None = a build without the settings layer)."""
    defaults = {}
    for kv in a.row_default:
        k, _, v = kv.partition('=')
        val = {'on': 1, 'off': 0}.get(v, int(v) if v.isdigit() else -1)
        if k not in _bo.ROW or not 0 <= val < _bo.ROW[k]['n']:
            raise SystemExit("--row-default wants ROW=VALUE (on/off or 0..n-1) with ROW in %s (got %r)"
                             % (_bo.ROW_ORDER, kv))
        defaults[k] = val
    trade = {'off': 0, 'on': 3, '30': 1, '40': 2, '50': 3}[a.trade_evo]
    o = dict(menu=a.options_menu == 'on', defaults=defaults, trade=trade,
             scripted_vanilla=a.scripted_vanilla == 'on', caught_marker=a.caught_marker == 'on',
             page=a.options_page == 'on')
    return o if (o['menu'] or o['trade'] or o['scripted_vanilla'] or o['caught_marker']) else None

def parse_args(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--text', required=True)
    ap.add_argument('--base-src', help='external base .pchtxt to merge (for --out-unified/--out-catch-unified '
                                  'with --base external)')
    ap.add_argument('--out-addon', help='optional add-on build (records only; for use on top of an '
                                        'external base mod)')
    ap.add_argument('--out-base', help='emit the built-in base ALONE (no EXP/catch modules): a base-only '
                                       'test build')
    ap.add_argument('--out-unified', help='the single mod: base + all modules in one .pchtxt')
    ap.add_argument('--base', default=None, choices=['self', 'external'],
                    help="base for --out-unified: self = the built-in base (base_self_records), needs "
                         "only --text; external = merge the external base given with --base-src. Default: external if "
                         "--base-src is given, else self.")
    ap.add_argument('--out-catch-unified', help='unified CATCH variant: EXP but keep the post-KO catch '
                                                'screen (no KO result rewrite; needs --base-src)')
    ap.add_argument('--profile', default='classic', choices=['classic', 'timed'],
                    help="classic (default): remove the 300s wild-battle timer; timed: keep it")
    ap.add_argument('--hp-gate', default='strict', choices=['slot6', 'strict'],
                    help="strict (default): the KO gate also requires the wild slot (12) and max HP > 0. "
                         "slot6: only a recorded wild HP of 0.")
    ap.add_argument('--exp-recipients', default='participants', choices=['party', 'participants'],
                    help="participants (DEFAULT): only Pokémon that entered the battle gain battle EXP "
                         "(module X; see --battle-exp-scope). party: the game's native Exp. Share, "
                         "the whole party gains (a test knob).")
    ap.add_argument('--battle-exp-scope', default='all', choices=['wild', 'all'],
                    help="with --exp-recipients participants: which battles are participants-only. "
                         "all (DEFAULT): every battle (wild + Trainer + scripted) gives EXP only to "
                         "the Pokémon that fought. wild: only wild KOs are participants-only; Trainer and "
                         "scripted battles keep the native party-wide EXP. Catch EXP is "
                         "a separate path (see --catch-exp).")
    ap.add_argument('--catch-exp', default='off', choices=['off', 'on'],
                    help="off (DEFAULT): catches give NO EXP to anyone and the 'Your Pokemon got N Exp. "
                         "Points!' screen is dropped (the catch result card still shows, EXP 0). Two "
                         "in-place patches: XF = catch-EXP formula 0x82D3F0 returns 0; XG = the reward "
                         "scene's 'can anyone gain EXP?' gate 0x830410 returns false (the game's own "
                         "no-EXP path). Battle EXP is untouched. on: the native whole-party catch EXP "
                         "and EXP screen.")
    ap.add_argument('--catch-action', default='x-lowhp',
                    choices=['off', 'zl-run', 'x-lowhp', 'x-anytime'],
                    help="x-lowhp (DEFAULT): the Y catch (named X-CATCH in the code; the button is Y): in "
                         "a wild battle, press Y when the wild Pokémon "
                         "is at <=25%% HP to catch it (native capture scene). x-anytime: same at any HP "
                         "(debug knob). zl-run: hold ZL + select RUN to catch (alternative build). off: "
                         "no catch action. x-* and zl-run are mutually exclusive by construction (an x "
                         "build has no ZL mirror/decide modules KM/K).")
    ap.add_argument('--x-stage', default='divert', choices=['observe', 'divert'],
                    help="X-CATCH build stage (only meaningful with --catch-action x-*). divert "
                         "(DEFAULT): the full feature (WCH eligibility + WCA id-5->CATCH + module G "
                         "flee-text suppression). observe: eligibility flag only, no routing — a "
                         "diagnostic build, zero game-visible change.")
    ap.add_argument('--input-source', default='handheld', choices=['handheld', 'all'],
                    help="ZL+RUN button source (only affects zl-run builds). handheld (default): hook "
                         "the Handheld poller. all (experimental): hook the shared post-poll helper "
                         "0x14b8a0 so ZL+RUN works on every controller style, not just Handheld.")
    ap.add_argument('--game', choices=sorted(GAME_INFO), default=None,
                    help="which game to build for: pikachu (Let's Go, Pikachu! 1.0.2) or eevee (Let's Go, "
                         "Eevee! 1.0.2). Default: detected from --text. The build refuses a text.bin from "
                         "the other game.")
    ap.add_argument('--options-menu', default='on', choices=['off', 'on'],
                    help="on (default): the in-game settings, saved in unused bits of the game's settings word: "
                         "one row per part this build has (Wild Battles, Quick Catch, Exp. Share, Catch Exp., "
                         "Battle Timer, Caught Marker, Return to Ball, Trade Evolution). off: no settings.")
    ap.add_argument('--options-page', default='on', choices=['off', 'on'],
                    help="on (default): the rows are on their own page, opened from a 'Wild Battles Settings' row "
                         "on the game's Options page (left/right on it). off: the rows follow the game's own rows.")
    ap.add_argument('--row-default', action='append', default=[], metavar='ROW=on|off',
                    help="an Options row's default: on/off, or a value number (quick: 0 off, 1 25%%, 2 50%%, 3 any HP; "
                         "share: 0 off, 1 Trainer battles, 2 on; trade: 0 off, 1 Lv30, 2 Lv40, 3 Lv50). Rows: wild, "
                         "quick, share, catch_exp, timer, marker, recall, trade. Repeatable.")
    ap.add_argument('--trade-evo', default='on', choices=['off', 'on', '30', '40', '50'],
                    help="on (default): trade evolutions (Kadabra, Machoke, Graveler, Haunter) can also happen by "
                         "level-up. With the settings on this is the Trade Evolution row (it starts Off; "
                         "--row-default trade= sets it). Without them, on = Lv. 50+, or give the level.")
    ap.add_argument('--scripted-vanilla', default='on', choices=['off', 'on'],
                    help="on (default): the built-in base's battle-event changes apply only to the mod's own wild "
                         "battles, so Snorlax, the Legendaries and other scripted fights play as normal.")
    ap.add_argument('--caught-marker', default='on', choices=['off', 'on'],
                    help="on (default): show a Poke Ball beside a wild Pokemon's HP box when its species is already "
                         "registered as caught.")
    ap.add_argument('--out-ips32', metavar='PATH',
                    help='with --out-unified: also write the same patch as IPS32 for Atmosphere (real '
                         'hardware); the file name Atmosphere expects is <build-id>.ips')
    ap.add_argument('--manifest', help='also write a build manifest (JSON) to this path')
    ap.add_argument('--verify-manifest', metavar='PATH',
                    help='after building, assert each output sha256 matches this committed manifest')
    a = ap.parse_args(argv)
    if a.base is None:                           # follow the input: an external base was given, or not
        a.base = 'external' if a.base_src else 'self'
    return a

def load_game_text(a):
    """text.bin for the build, with the game detected (or forced by --game) and its hook sites checked."""
    text = Path(a.text).read_bytes()
    found = game_of_text(text)
    if a.game and found and a.game != found:
        raise SystemExit("--text is a %s text.bin, but --game %s was asked for" % (GAME_INFO[found]['title'], a.game))
    game = a.game or found
    if game is None:
        raise SystemExit("--text is not a known 1.0.2 text.bin (Pikachu or Eevee); pass --game to force one")
    set_game(game)
    print("game:", GAME_INFO[game]['title'], "1.0.2 (build-id %s)" % NSOBID[:32])

    verify_preimages(text)
    return text

def build_caves(a, text):
    """Lay out and verify every module for these options; (caves, the records outside the hook/cave pairs)."""
    global NORECALL_ENC, BUDDY_FORCE
    BUDDY_FORCE = (a.base == 'self')                 # UE's helper lives in the built-in base's free space
    if a.catch_action in ('x-lowhp', 'x-anytime'):
        NORECALL_ENC = 'delta'
    options = cli_options(a)
    if options and a.base != 'self':
        print("note: an external base has no room for the options layer; building without it")
        options = None
    diagnostics = ()
    caves = lay_out(text, a.profile, a.hp_gate, a.exp_recipients, a.catch_action,
                    input_source=a.input_source, x_stage=a.x_stage, diagnostics=diagnostics,
                    exp_scope=a.battle_exp_scope, catch_exp=a.catch_exp, options=options)
    verify_caves(text, caves)
    verify_scratch_use(caves)
    verify_cave_padding(text, caves)
    extras = extra_records(caves)
    if extras:
        assert not a.out_catch_unified, "x-* catch actions have no catch-variant build"
        wc = {k: c for k, c in caves.items() if k in ('WCH', 'WCA')}
        used = sum(len(c['words']) * 4 for c in wc.values())
        pad_end = max((c['base'] + len(c['words']) * 4 for c in caves.values()
                       if c.get('region') is None), default=CAVE_BASE)
        print("X-CATCH [%s/%s]: %d WC cave bytes in the 0xD32xxx pad; pad end %s (%d B free of pad)"
              % (a.catch_action, a.x_stage, used, hex(pad_end), CAVE_END_PAGE - pad_end))
        for k in ('WCH', 'WCA'):
            if k in wc:
                print("  %-4s cave %s  %3d B  (hook %s)" % (k, hex(wc[k]['base']),
                                                            len(wc[k]['words']) * 4, hex(wc[k]['hook'])))
    return caves, extras

def write_outputs(a, text, caves, extras):
    """Write every requested mod file; {output name: (path, records)}."""
    outputs = {}

    if a.out_base:
        base = base_self_records(text)
        verify_base_self(text, base)
        overlap_check('base-self', base)
        emit(base, a.out_base,
             ["// Wild Battles Remastered - built-in base ONLY (no EXP/catch modules).",
              "// Base-only test build: enables turn-based wild battles.",
              "// pchtxt addr = memory offset + 0x100."])
        print("wrote", a.out_base, "(%d records, self-verified; base-only)" % len(base))
        outputs['base'] = (a.out_base, base)

    if a.out_addon:
        addon = variant_records('addon', caves) + extras
        emit(addon, a.out_addon,
             ["// Wild Battles Remastered - ADD-ON: modules only; needs a base mod enabled too.",
              "// pchtxt addr = memory offset + 0x100."])
        print("wrote", a.out_addon)
        outputs['addon'] = (a.out_addon, addon)

    if a.out_unified:
        assert a.base == 'self' or a.base_src, "--base-src required for --out-unified (or build with --base self)"
        base, hdr = unified_base(text, a.base, a.base_src)
        merged = base + variant_records('main', caves) + extras
        verify_marker(merged)
        overlap_check('unified', merged)
        merged = disjoint(merged)
        emit(merged, a.out_unified, hdr)
        print("wrote", a.out_unified, "(%d records, base=%s, overlap-checked)" % (len(merged), a.base))
        outputs['unified'] = (a.out_unified, merged)
        if a.out_ips32:
            emit_ips32(merged, a.out_ips32)
            print("wrote", a.out_ips32, "(IPS32 for Atmosphere; name it %s)" % ips32_name())
            outputs['ips32'] = (a.out_ips32, merged)

    if a.out_catch_unified:
        assert a.base_src, "--base-src required for --out-catch-unified"
        # Catch variant: faint + crash-guard + native XP, but NO result rewrite -> the vanilla
        # post-KO catch screen stays. It includes hook E purely to clear wild_encounter after each
        # battle (its evolution redirect stays inert: only cave_result_rewrite sets evolve_pending, and it is left out).
        merged2 = load_base_src(a.base_src) + variant_records('catch', caves)
        verify_marker(merged2)
        overlap_check('catch', merged2)
        merged2 = disjoint(merged2)
        emit(merged2, a.out_catch_unified,
             ["// Wild Battles Remastered - CATCH VARIANT (unified). Battle -> KO -> native EXP ->",
              "// the normal catch screen (you can still catch what you battled).",
              "// Mutually exclusive with the main unified mod: enable only ONE of them.",
              "// pchtxt addr = memory offset + 0x100."])
        print("wrote", a.out_catch_unified, "(%d records, overlap-checked)" % len(merged2))
        outputs['catch'] = (a.out_catch_unified, merged2)

    return outputs

def verify_outputs(outputs, manifest_path):
    """Each written file must hash to what the committed manifest pins."""
    import json, hashlib
    ref = json.loads(Path(manifest_path).read_text())
    refout = ref.get('outputs', {})
    for name, (p, _recs) in outputs.items():
        want = refout.get(name, {}).get('sha256')
        got = hashlib.sha256(Path(p).read_bytes()).hexdigest()
        assert want is not None, ("no committed hash for output", name, manifest_path)
        assert got == want, ("artifact drift vs committed manifest", name, got, want)
    print("verified %d artifact hash(es) against %s" % (len(outputs), manifest_path))

def main():
    a = parse_args()
    text = load_game_text(a)
    caves, extras = build_caves(a, text)
    outputs = write_outputs(a, text, caves, extras)
    if a.manifest:
        write_manifest(a.manifest, a.profile, caves, outputs, a.hp_gate, a.exp_recipients, a.catch_action,
                       exp_scope=a.battle_exp_scope, catch_exp=a.catch_exp, base=a.base)
        print("wrote", a.manifest)

    if a.verify_manifest:
        verify_outputs(outputs, a.verify_manifest)

if __name__ == '__main__':
    main()
