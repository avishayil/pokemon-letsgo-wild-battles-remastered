"""Address shifts from Let's Go, Pikachu! 1.0.2 to Let's Go, Eevee! 1.0.2 (.text only).

Generated from the two games' code (instruction-by-instruction comparison with PC-relative fields
masked); do not edit by hand. Each row is (Pikachu start, Pikachu end, shift in bytes): an address in
[start, end) is at address + shift in Eevee. Both games are built from the same source; these rows cover
99.95% of the code. Only numbers, no game code. Everything outside .text (rodata, data, bss) and the
end-of-text padding keeps its address. A build checks every hook site it patches against the text.bin
of the game it builds for.
"""

PIKACHU_TEXT_SHA256 = '87e4a41b25b0dac415dfd3bd1e8e84c3f6cd59addc94f7f7e46b0260abcc625f'
EEVEE_TEXT_SHA256 = 'e15ebe15eb8d2a52bd87ddc02d210d497c8f1c8ea37cf0cb3ed1c03dfae00729'

EEVEE_RUNS = [
    (0x0000030, 0x0070130, +0),
    (0x00701B0, 0x00818B0, +0),
    (0x0081970, 0x00A44B0, +16),
    (0x00A44F0, 0x00A4DB0, +56),
    (0x00A4E30, 0x00A75F0, +48),
    (0x00A7670, 0x013BF30, +48),
    (0x013BF70, 0x0199430, +48),
    (0x01994B0, 0x01A7730, +48),
    (0x01A77B0, 0x01A79B0, +48),
    (0x01A7A30, 0x01BC7B0, +48),
    (0x01BC830, 0x01BCAF0, +48),
    (0x01BCB70, 0x01C7F30, +48),
    (0x01C7F70, 0x01C9A30, +48),
    (0x01C9A70, 0x01C9AB0, +48),
    (0x01C9AF0, 0x01D69B0, +48),
    (0x01D69F0, 0x01DACF0, +48),
    (0x01DAD30, 0x027F830, +48),
    (0x027F870, 0x0291E70, +48),
    (0x0291EF0, 0x029AFF0, +48),
    (0x029B030, 0x03077B0, +48),
    (0x03077F0, 0x030CFB0, +48),
    (0x030CFF0, 0x031B970, +48),
    (0x031B9B0, 0x032F670, +48),
    (0x032F6F0, 0x0330030, +96),
    (0x0330070, 0x033F8F0, +96),
    (0x033F970, 0x0772D30, +96),
    (0x0772D70, 0x077F1F0, +96),
    (0x077F2B0, 0x077F8F0, +96),
    (0x077F970, 0x0780830, +96),
    (0x07808F0, 0x0789970, +96),
    (0x07899F0, 0x0789AF0, +96),
    (0x0789DF0, 0x0789E30, -44),
    (0x0789E70, 0x0789EB0, -44),
    (0x078A070, 0x078A0B0, -44),
    (0x078A170, 0x07C78F0, -48),
    (0x07C7970, 0x082CFB0, -48),
    (0x082D030, 0x0898E30, -48),
    (0x0898EB0, 0x089B570, -48),
    (0x089B670, 0x08A7630, -48),
    (0x08A7670, 0x08AF1B0, -48),
    (0x08AF230, 0x090EA70, -48),
    (0x090EC30, 0x0925EF0, -48),
    (0x0925F70, 0x09260B0, -48),
    (0x0926130, 0x092B5B0, -48),
    (0x092B670, 0x094FC70, -16),
    (0x094FCB0, 0x0997730, -16),
    (0x09977B0, 0x0AB1970, -16),
    (0x0AB1A70, 0x0AB3C30, -16),
    (0x0AB3C70, 0x0D32BA8, -16),
]
