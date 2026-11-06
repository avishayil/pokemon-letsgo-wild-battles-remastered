# Changelog

## v1.0.0-beta

First public beta, for both *Pokémon: Let's Go, Pikachu!* and *Pokémon: Let's Go, Eevee!* (v1.0.2).

### Battles
- Turn-based wild battles, from the mod's own built-in base: one mod per game, with the same features.
- Real EXP for a wild knockout: the game's own amounts, level-ups, new moves and evolutions.
- Only the Pokémon that battled earn EXP, in every battle (wild and Trainer). The bench gets nothing,
  and the "party Pokémon in waiting" message is gone.
- A defeated wild Pokémon faints cleanly (no "return to ball" animation) and the battle ends straight
  back to the overworld.
- No 5-minute wild-battle timer.
- Works from the start of a new game: before "Play with Pikachu/Eevee" is unlocked, the mod turns the
  partner on for its own wild battles (and back off after), so your partner appears and battles normally.
  Your progress and save are untouched.

### Catching
- Press Y when the wild Pokémon is at 25% HP or less to enter the game's own catch scene.
- Catching gives no EXP to anyone, and the "Your Pokémon got N Exp. Points!" screen is skipped. The
  catch summary and its candy reward still show.
- The "Got away safely!" message no longer flashes before the catch scene.

### Settings in the game
- The mod has its own page in the game's Options menu: press A (or left or right) on the last row, Wild
  Battles Settings, to open it. Wild Battles On/Off (Off = the game's normal catching), Quick Catch (Off, 25%, 50%,
  any HP), Exp. Share (Off, Trainer battles only, On), Catch Exp., Battle Timer, Caught Marker, Return to
  Ball and Trade Evolution (Off, Lv. 30, 40, 50). Choices are saved in the save file, in unused bits of the
  game's own settings; a save that never had the mod starts at the defaults.
- Shown in the game's language, all ten. On/Off are the game's own words; the rest is our translation.

### Extras
- Caught marker: a Poké Ball beside the wild Pokémon's HP box when you've caught that species.
- Trade evolution by level (optional, starts Off): Kadabra, Machoke, Graveler and Haunter (and Alolan
  Graveler) evolve on level-up from the chosen level, the same way as a level-up evolution. Trading is
  unchanged.
- Snorlax, the Legendaries and other scripted fights play as in the original game: the base's battle
  changes apply only to the mod's own wild battles.
- An Atmosphère download for a real Switch (the same patch as IPS32; untested on hardware yet).

### Under the hood
- The Eevee mod is the Pikachu mod with every address moved through a generated shift table. Before
  it writes anything, the generator checks that every hook site holds the instruction it expects, in
  the game being built.
- The settings code lives in a part of the game's code that can never run (a protobuf text parser the
  game reaches only for data it doesn't have); the generator pins those bytes and refuses any other.
- Every patch record is disjoint, so the result doesn't depend on the order an emulator applies them in.

### Known limitations
- Legendary and special encounters (Snorlax, the legendary birds, Mewtwo, the Power Plant Electrode)
  are checked against the game's code but not played with the mod yet. Save before one.
- The Atmosphère download hasn't been tried on a real Switch.
- An empty party bar shows briefly after a catch.
- Tested on Eden only. *Let's Go, Eevee!* was played through its first routes so far (wild battles,
  catching, a Trainer battle and the settings page); the rest of its story hasn't been played yet.
