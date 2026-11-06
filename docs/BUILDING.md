# Building from source

You only need this to build the mod yourself, or check that the released mod
matches this source. To just play, download the release zip instead (see the
[installation guide](HOW_TO_USE.md)).

`tools/build_patch.py` generates the mod from one input: the code section (`text.bin`) of your own
v1.0.2 executable of *Pokémon: Let's Go, Pikachu!* or *Pokémon: Let's Go, Eevee!*. The
generator tells the two apart by `text.bin` and builds the matching mod. Nothing from either game is in
this repository.

## 1. Tools

- Python 3.10+ and `capstone`: `python3 -m pip install capstone`
- hactool ([SciresM/hactool](https://github.com/SciresM/hactool)), built from source:
  `cp config.mk.template config.mk && make`
- From your own console: `prod.keys`, and the v1.0.2 update as an `.nsp` (if you have the cartridge,
  dump the update as installed on your Switch).

## 2. Make `text.bin` from your own dump

The steps are the same for both games; only the file names and the expected values differ:

| | *Let's Go, Pikachu!* 1.0.2 | *Let's Go, Eevee!* 1.0.2 |
|---|---|---|
| Ticket in the update NSP | `010003f003a348000000000000000007.tik` | `0100187003a368000000000000000007.tik` |
| Program NCA | `2b7730a9e56498bbafac2002d4908c6b.nca` | `6c71555b4c03a9ff527ee45c8aa4ba45.nca` |
| Build-id (`exefs/main` +0x40) | `c208db6a4ef4361f28b8402be0f7cd9a` | `5831ec64d6b696fd69e69505821b97b1` |
| `text.bin` SHA-256 | `87e4a41b25b0dac415dfd3bd1e8e84c3f6cd59addc94f7f7e46b0260abcc625f` | `e15ebe15eb8d2a52bd87ddc02d210d497c8f1c8ea37cf0cb3ed1c03dfae00729` |

Both `text.bin` files are exactly 13,839,360 bytes. The commands below are for Pikachu; for Eevee,
use the Eevee names from the table.

```sh
HT=/path/to/hactool; K=/path/to/prod.keys; OUT=game-files      # git-ignored; never commit game files
mkdir -p "$OUT"
# unpack the v1.0.2 update NSP
$HT -k $K -t pfs0 --outdir=$OUT/nsp "Pokemon Lets Go Pikachu v1.0.2.nsp"
# find its Program NCA (for v1.0.2: 2b7730a9e56498bbafac2002d4908c6b.nca)
for n in $OUT/nsp/*.nca; do $HT -k $K -t nca "$n" | grep -q 'Content Type: *Program' && echo "$n"; done
# the title key is the 16 bytes at 0x180 of the ticket
TK=$(xxd -s 0x180 -l 16 -p $OUT/nsp/010003f003a348000000000000000007.tik | tr -d '\n')
# extract the executable
$HT -k $K --titlekey=$TK -t nca --exefsdir=$OUT/exefs $OUT/nsp/2b7730a9e56498bbafac2002d4908c6b.nca
# the build-id MUST start with c208db6a4ef4361f28b8402be0f7cd9a, or this is not v1.0.2
xxd -s 0x40 -l 16 -p $OUT/exefs/main
# decompress it, then keep only the code section
$HT -k $K -t nso0 --uncompressed=$OUT/main_dec.bin $OUT/exefs/main
dd if=$OUT/main_dec.bin of=$OUT/text.bin bs=256 skip=1 count=54060
```

Check it: `text.bin` must be exactly 13,839,360 bytes, with the SHA-256 for your game in the table above.

## 3. Build and verify the mod

```sh
python3 -I tools/build_patch.py --text game-files/text.bin \
  --out-unified "mods/Wild Battles Remastered/exefs/wild_battles_remastered.pchtxt" \
  --verify-manifest build-manifest.json
```
`verified 1 artifact hash(es)` means your build is byte-for-byte the committed mod in [`mods/`](../mods/).
For Eevee, build into `mods/Eevee/Wild Battles Remastered/exefs/wild_battles_remastered.pchtxt` and verify
against `build-manifest.eevee.json` (add `--game eevee` to make the choice explicit; a `text.bin` from the
other game is refused).
The generator checks every patch before it writes it: it decodes, returns where it should, keeps the
stack balanced, doesn't overlap another patch, and no two parts of the mod use the same scratch byte.

To package the importable zips exactly like a release:
```sh
tools/package_release.sh v1.0.0-beta --text game-files/text.bin
#  -> dist/v1.0.0-beta/WildBattlesRemastered-v1.0.0-beta.zip (Eden)
#     dist/v1.0.0-beta/WildBattlesRemastered-Atmosphere-v1.0.0-beta.zip (Atmosphère)  +  SHA256SUMS
tools/package_release.sh v1.0.0-beta --text game-files/eevee/text.bin --game eevee
#  -> the same two for Eevee (SHA256SUMS covers all four)
```
The manifests pin both the `.pchtxt` and the IPS32 file (`outputs.unified` / `outputs.ips32`).

## 4. Custom builds

Everything a player would change is a setting in the game, so a custom build is rarely needed. The
generator still takes every choice on the command line (`python3 -I tools/build_patch.py --help`), for
example a build without the settings page (`--options-page off`, the rows then follow the game's own
Options rows), different starting values (`--row-default quick=3 --row-default share=1`), or none of the
in-game settings at all (`--options-menu off --trade-evo off --scripted-vanilla off --caught-marker off`:
a build without the settings layer). `--out-ips32 <file>` also writes the Atmosphère patch.

## 5. Tests

The tests need no game files. Run each one with `python3 -I tests/<name>.py`. CI runs them all on every
push to `main` and every pull request (`.github/workflows/ci.yml`), and also lints the Python with ruff (the rules are in `ruff.toml`) and
the shell scripts with shellcheck. With `WB_TEXT_BIN=game-files/text.bin` (and/or
`WB_TEXT_BIN_EEVEE=game-files/eevee/text.bin`) set, `tests/test_mod_spec.py` also checks the default
build of each game against its committed mod, and that a `text.bin` from the other game is refused;
`tests/test_options.py` builds the settings layer in many combinations through every generator check. With
`WB_ROMFS` pointing at the romfs `bin/` folder of your own dump, `tests/test_options.py` also checks the
game's own On/Off labels in all ten languages and the battle HUD's pane names.
