# Installing Wild Battles Remastered

## What you need

- *Pokémon: Let's Go, Pikachu!* or *Pokémon: Let's Go, Eevee!*, version 1.0.2, from your own legally
  owned copy.
- A Nintendo Switch emulator that supports `.pchtxt` mods (developed and tested on
  [Eden](https://git.eden-emu.dev/eden-emu/eden)), or a Switch running Atmosphère (untested so far).

> This project includes no game files. You must own the game.

## Steps

1. Download the mod for your game from the [Releases](../../releases) page:
   `WildBattlesRemastered-v1.0.0-beta.zip` for *Let's Go, Pikachu!*, or
   `WildBattlesRemastered-Eevee-v1.0.0-beta.zip` for *Let's Go, Eevee!*. Each mod only works on its own
   game. (On a real Switch, see [Atmosphère](#on-a-real-switch-atmosphère) below.)
2. Back up your save. Always do this before trying a new mod.
3. Add the mod to the game. In Eden, right-click the game → **Open Mod Data Location** (or use
   **Import Mod from ZIP**), and add the `.zip`. You should end up with
   `Wild Battles Remastered/exefs/wild_battles_remastered.pchtxt` inside the game's mod folder. Make sure
   **Wild Battles Remastered** is ticked under the game's **Add-Ons**.
4. Use only this mod. It is self-contained, so turn off any other wild-battle, EXP or catching mod.
5. Play. Walk into a wild Pokémon and you'll drop into a turn-based battle. This works from the very
   start of a new game too.

## On a real Switch (Atmosphère)

Download `WildBattlesRemastered-Atmosphere-v1.0.0-beta.zip` (Pikachu) or
`WildBattlesRemastered-Eevee-Atmosphere-v1.0.0-beta.zip` (Eevee) and copy its `atmosphere` folder to the
root of your SD card, merging folders. You end up with
`atmosphere/exefs_patches/Wild Battles Remastered/<build-id>.ips`; Atmosphère applies it when the game
starts. It is the same patch as the Eden mod, but nobody has tried it on a real Switch yet; please report
how it goes in [Issues](../../issues). Play offline only: don't use it online.

## Settings

Open **Options** in the game's menu and go to the last row, **Wild Battles Settings**. Press **A** (or
left or right) on it to open the mod's own page, change what you like, and press **B** to go back (the game asks
whether to keep the changes). The settings are kept in your save, and start at the defaults below.

| Setting | Choices | Starts at |
|---|---|---|
| Wild Battles | On · Off (Off = the game's normal catching) | On |
| Quick Catch | Off · 25% · 50% · Any HP (press **Y** at or below that HP) | 25% |
| Exp. Share | Off (only battlers) · Trainers (whole party in Trainer battles) · On (whole party) | Off |
| Catch Exp. | On (the game's normal catch EXP) · Off (no EXP from catching) | Off |
| Battle Timer | On (the game's 5-minute limit in wild battles) · Off | Off |
| Caught Marker | On (a Poké Ball by the HP box of a species you've caught) · Off | On |
| Return to Ball | On (the recall animation when a wild Pokémon faints) · Off | Off |
| Trade Evolution | Off · Lv. 30 · Lv. 40 · Lv. 50 (Kadabra, Machoke, Graveler, Haunter) | Off |

## In a wild battle

| You do | What happens |
|---|---|
| Knock the wild Pokémon out | It faints; the Pokémon that fought earn EXP (real amounts, level-ups, evolutions); the rest of the team gets none. You return straight to the overworld. |
| Lower it to 25% HP or less, then press **Y** | You enter the game's own catch scene and throw a ball, under the normal catch rules. A catch gives no EXP. (The HP level is the **Quick Catch** setting.) |
| Press **Y** above that HP | The normal Y menu, as in the base game. |
| Select **RUN** | You flee, as in the base game. |

Trainer battles play as normal, except that only the Pokémon that fought earn EXP.

## Having trouble?

- Nothing changed? Check that the mod is enabled, that it's the zip for your game (Pikachu and Eevee
  each have their own), and that the game is on version 1.0.2 (the update must be installed).
- Other mods? Turn off any other wild-battle or EXP mod.
- Before a legendary or special encounter (Snorlax, the legendary birds, Mewtwo, the Power Plant
  Electrode): save first. They should play as in the original game, but nobody has played one with the
  mod yet.
- Want the original game back for a while? Set **Wild Battles** to Off on the settings page.
- Still stuck? Open an issue on the [Issues](../../issues) page.
