# Credits

This project builds on other people's work: mods that first showed what was possible, research that
explained how the game works, and the free tools used to study it. Thank you to all of them.

## Original mods and inspiration

### Rico Santos: *Wild Battles* (Let's Go, Pikachu! / Eevee! 1.0.2)
The first mod to turn Let's Go's overworld encounters into real turn-based wild battles.
[GameBanana](https://gamebanana.com/mods/729162) ·
[GBAtemp thread](https://gbatemp.net/threads/mod-release-wild-battles-in-pokemon-lets-go-eevee-pikachu-1-0-2-versions.685505/) ·
[Reddit thread](https://www.reddit.com/r/PokemonROMhacks/comments/1x9nktc/mod_wild_battles_in_pok%C3%A9mon_lets_go_eevee_pikachu/)

### Rico Santos: *Win or Catch* (Let's Go 1.0.2)
The first mod to let you catch a wild Pokémon from inside the battle once it's weakened.
[Reddit thread](https://www.reddit.com/r/PokemonROMhacks/comments/1xl3iib/mod_win_or_catch_pok%C3%A9mon_lets_go_102_versions/)

### feelingdev: *The Better Let's Go* (Let's Go 1.0.2)
Showed that the game's own Options menu can carry a mod's settings, translated into every game language,
and suggested the caught marker and level-up trade evolution.
[Repository](https://github.com/feelingdev/The-Better-Lets-Go)

*Wild Battles Remastered* is an independent implementation inspired by these mods. It doesn't include
their files or code, and you don't need them installed.

## Research references

### raulzrrs: *letsgo-recomp*
A recompilation of Let's Go, Eevee! whose wild-battle runtime documents how the game's native battles
end: fainting, EXP, the battle result and the after-battle evolution. Its addresses are for a different
game build; this project found the equivalents for Pikachu 1.0.2 (and then Eevee 1.0.2) on its own.
[Repository](https://github.com/raulzrrs/letsgo-recomp) ·
[`runtime/hle/wildbattle.c`](https://github.com/raulzrrs/letsgo-recomp/blob/main/runtime/hle/wildbattle.c) ·
[`docs/MODO-BATALHA.md`](https://github.com/raulzrrs/letsgo-recomp/blob/main/docs/MODO-BATALHA.md) ·
[`docs/HISTORICO.md`](https://github.com/raulzrrs/letsgo-recomp/blob/main/docs/HISTORICO.md)

## Emulator

| Project | Used for | Link |
|---|---|---|
| Eden | Running and testing the mod; its GDB stub (memory reads and write-watchpoints) ran every live experiment | [eden-emu.dev](https://git.eden-emu.dev/eden-emu/eden) · [GitHub mirror](https://github.com/eden-emulator/mirror) |
| MoltenVK (Khronos Group) | Vulkan on macOS, under Eden | [GitHub](https://github.com/KhronosGroup/MoltenVK) |

## Reverse-engineering and build tools

| Tool | Author | Used for | Link |
|---|---|---|---|
| Ghidra | NSA | Decompiling the game's battle and reward logic | [GitHub](https://github.com/NationalSecurityAgency/ghidra) |
| Capstone | Capstone Engine | Disassembly in the analysis tools, and the generator's self-check of every emitted patch | [capstone-engine.org](https://www.capstone-engine.org/) |
| radare2 | radareorg | Ad-hoc binary inspection | [GitHub](https://github.com/radareorg/radare2) |
| hactool | SciresM | Extracting the game's executable from your own dump | [GitHub](https://github.com/SciresM/hactool) |
| 4NXCI (0.9-alpha.1) | The-4n (portions © SciresM, from hactool) | Converting a cartridge dump (XCI) to NSP | [GitHub (tiliarou mirror)](https://github.com/tiliarou/4NXCI) · [GameBrew](https://www.gamebrew.org/wiki/4NXCI_Switch) |
| pbseqtool | lincoln-lm | Decoding the game's battle sequence files | [GitHub](https://github.com/lincoln-lm/pbseqtool) |
| IPSwitch `.pchtxt` | 3096 | The patch-text format this mod is distributed in | [GitHub](https://github.com/3096/ipswitch) |
| Atmosphère IPS32 patches | Atmosphère-NX | The patch format of the real-hardware download | [GitHub](https://github.com/Atmosphere-NX/Atmosphere) |

## This project
Reverse engineering, address mapping, the patch generator, the in-game settings and the documentation:
**avishayil**.

## Legal
Pokémon, *Pokémon: Let's Go, Pikachu!* and *Pokémon: Let's Go, Eevee!* are © Nintendo, Creatures Inc., GAME FREAK inc. and The
Pokémon Company. This is an unofficial fan project, not affiliated with or endorsed by them. It contains
no game code or assets, only address-based patches you apply to your own legally obtained copy. See
[DISCLAIMER.md](DISCLAIMER.md).
