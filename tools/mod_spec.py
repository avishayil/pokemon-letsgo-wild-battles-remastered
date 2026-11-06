#!/usr/bin/env python3
"""mod_spec.py — the mod's features as data, and a library front end to the generator.

It holds:
  * FEATURES — a declarative registry of every user-selectable feature and which generator module(s)
    / flags it maps to.
  * BuildSpec — a JSON-serializable record of a feature selection (the replayable "build spec").
  * spec_to_args() — maps a spec to tools/build_patch.py's generator knobs.
  * validate() — coherence/feasibility engine: hard ERRORS (block) + WARNINGS (inform).
  * layout_fit() — dry-run cave-space fit (no exceptions), for reporting how much of the pad a build uses.
  * build() — drives build_patch as a library: emit the .pchtxt + manifest, return a summary, and
    optionally package the reproducible Eden-importable .zip.
"""
from __future__ import annotations
import contextlib, dataclasses, hashlib, json, os, subprocess, sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_patch as bp

PAD_LO, PAD_HI = bp.CAVE_BASE, bp.CAVE_END_PAGE  # the end-of-.text padding the caves live in
PAD_SIZE = PAD_HI - PAD_LO                        # 1112 B

CATCH_MODES = ('none', 'quick-catch', 'x-catch-lowhp', 'x-catch-anytime')

# ----- feature registry -------------------------------------------------------------------------
# Each feature: key, friendly name, one-line description, class, the build_patch module key(s) it
# turns on, and notes. `cls` drives UI grouping and the default warn/block policy.
@dataclasses.dataclass(frozen=True)
class Feature:
    key: str
    name: str
    desc: str
    cls: str                 # 'core' | 'safety' | 'polish' | 'optional' | 'menu'
    modules: tuple           # build_patch module keys this feature contributes (informational)

NULL_GUARDS = ('C', 'UG', 'UH', 'UHP', 'UI', 'UJ', 'UK', 'UM', 'UE') + tuple(bp.BUDDY_KEYS)

FEATURES = [
    Feature(key='core_faint', name='Wild KO → faint', cls='core', modules=('A',),
            desc='A defeated wild Pokémon faints instead of instant-ending the battle (the whole feature hinges on this).'),
    Feature(key='core_result', name='No catch screen after KO', cls='core', modules=('ST',),
            desc='Rewrite the battle result so a KO ends cleanly with no post-KO catch screen (strict verified-KO gate).'),
    Feature(key='core_reflect', name='Wild-identity capture', cls='core', modules=('R',),
            desc='Record the wild Pokémon (slot/HP/species) — feeds the strict KO gate, participants EXP, and X-CATCH HP.'),
    Feature(key='core_evolution', name='After-battle evolution', cls='core', modules=('E',),
            desc='Route the after-battle transition so a KO-induced level-up evolves; also clears the per-encounter flags.'),
    Feature(key='core_exp', name='Native wild-KO EXP', cls='core', modules=('P',),
            desc="Award the game's real EXP for a wild knockout (bypass the wild-battle EXP-eligibility gate)."),
    Feature(key='core_marker', name='Wild-encounter marker', cls='core', modules=('M',),
            desc='Set the encounter flag that scopes every effect to wild battles only. LOAD-BEARING — cannot be removed.'),
    Feature(key='crash_guard', name='Crash guards', cls='safety', modules=NULL_GUARDS,
            desc='Prevent null-pointer crashes on resource-less wild models: after a faint, and in the battle intro '
                 'before "Play with Pikachu/Eevee" is unlocked.'),
    Feature(key='no_recall', name='No ball-recall on faint', cls='polish', modules=('N',),
            desc='Disarm the trainer-style "return to ball" animation when a wild Pokémon faints (wild battles only, fail-open).'),
    Feature(key='no_timer', name='No 5-minute battle timer', cls='polish', modules=('A',),
            desc='Remove the 300-second MATCH TIME limit in wild battles (genuine special battles keep theirs).'),
    Feature(key='participants', name='Participants-only EXP', cls='optional', modules=('X',),
            desc='Only the Pokémon that took part in a battle earn EXP — the rest of your party gets none. Applies to '
                 'every battle (wild and Trainer) by default; the Advanced "battle EXP scope" can limit it to wild '
                 'battles. Off = native party-wide EXP.'),
    Feature(key='no_catch_exp', name='No EXP from catching', cls='optional', modules=('XF', 'XG'),
            desc='Catching a Pokémon gives NO EXP to anyone and skips the "Your Pokémon got N Exp. Points!" screen '
                 "(the catch summary card still shows, with EXP 0). Battle EXP is unaffected. Off = the game's "
                 'native party-wide catch EXP.'),
    Feature(key='flee_text', name='Suppress "Got away safely!"', cls='polish', modules=('G',),
            desc='Hide the flee message that would otherwise flash before the capture scene (catch modes only).'),
    Feature(key='options_menu', name='In-game settings page', cls='menu',
            modules=('OSTR', 'OB1', 'OB2', 'OTX', 'OTG', 'OAR', 'ODS', 'OLD', 'OAP', 'OCH', 'OLK', 'OPQ', 'ODI',
                     'OGW1', 'OGW2', 'OGB', 'OFNB', 'OXG', 'ONG', 'OTM', 'OXFG', 'OXGG', 'OQG'),
            desc="A Wild Battles Settings page inside the game's Options turns the mod's parts on or off at any "
                 'time (only the parts this build has). Choices are kept in your save; the defaults are the ones '
                 'chosen here.'),
    Feature(key='trade_evolution', name='Trade evolution by level', cls='optional', modules=('TE',),
            desc='Kadabra, Machoke, Graveler and Haunter (and Alolan Graveler) also evolve by levelling up. With '
                 'the settings page it is a row with the level to reach (default below).'),
    Feature(key='scripted_vanilla', name='Snorlax & Legendaries as normal', cls='safety',
            modules=('OGW1', 'OEVS', 'OEC', 'OEE', 'OEF'),
            desc="The built-in base's battle-event changes apply only to the mod's own wild battles, so Snorlax, "
                 'the Legendaries and other scripted fights play exactly as in the original game.'),
    Feature(key='caught_marker', name='Caught marker', cls='polish', modules=('CMF', 'CM'),
            desc="A Poké Ball appears beside the wild Pokémon's HP box when you have already caught that species."),
]
# Features made of modules that are simply left out when the feature is off (the others change a
# generator option instead, see spec_to_args).
OMITTABLE = ('core_faint', 'crash_guard', 'core_result', 'core_reflect', 'core_evolution', 'core_exp',
             'core_marker', 'no_recall')
FEATURE_BY_KEY = {f.key: f for f in FEATURES}


# ----- spec model -------------------------------------------------------------------------------
@dataclasses.dataclass
class BuildSpec:
    # core / optional booleans (True = feature ON)
    core_faint: bool = True
    core_result: bool = True
    core_reflect: bool = True
    core_evolution: bool = True
    core_exp: bool = True
    core_marker: bool = True
    crash_guard: bool = True
    no_recall: bool = True
    no_timer: bool = True
    participants: bool = True
    no_catch_exp: bool = True
    flee_text: bool = True
    # parametric choices
    catch_mode: str = 'x-catch-lowhp'            # one of CATCH_MODES
    input_source: str = 'handheld'               # quick-catch only: 'handheld' | 'all'
    battle_exp_scope: str = 'all'                # participants EXP: 'all' battles | 'wild' only
    strict_ko_gate: bool = True                  # strict (default) vs the older slot6 gate
    norecall_enc: str = 'table'                  # 'table' | 'delta' (32 B smaller); the Y catch builds force delta
    # the in-game Options layer + extras (tools/build_options.py; built-in base only)
    options_menu: bool = True
    options_page: bool = True                    # the rows on their own page (a row on the game's page opens it)
    trade_evolution: bool = True
    scripted_vanilla: bool = True
    caught_marker: bool = True
    # each Options row's default value (a save that never saw the mod starts here); 0 = Off
    row_wild: int = 1
    row_quick: int = 1                           # 0 Off, 1 25%, 2 50%, 3 any HP
    row_share: int = 0                           # 0 Off (battlers only), 1 Trainer battles, 2 On
    row_catch_exp: int = 0
    row_timer: int = 0
    row_marker: int = 1
    row_recall: int = 0
    row_trade: int = 0                           # 0 Off, 1 Lv. 30, 2 Lv. 40, 3 Lv. 50

    @staticmethod
    def shipped_default() -> 'BuildSpec':
        """The shipped product (X-CATCH low-HP + participants + no catch EXP + no-timer + strict + N/C/G)."""
        return BuildSpec()

    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), indent=2) + '\n'

    @staticmethod
    def from_json(text: str) -> 'BuildSpec':
        d = json.loads(text)
        fields = {f.name for f in dataclasses.fields(BuildSpec)}
        unknown = set(d) - fields
        if unknown:
            raise ValueError('unknown spec keys: %s' % sorted(unknown))
        return BuildSpec(**d)


# ----- spec → generator knobs -------------------------------------------------------------------
_CATCH_ACTION = {'none': 'off', 'quick-catch': 'zl-run',
                 'x-catch-lowhp': 'x-lowhp', 'x-catch-anytime': 'x-anytime'}

def spec_to_args(spec: BuildSpec) -> dict:
    """Translate a spec into build_patch.lay_out()/module_table() keyword args (+ the NORECALL_ENC
    global). `omit` is the set of base modules the user turned off; X is controlled via
    exp_recipients, the catch modules via catch_action, G via omit."""
    if spec.catch_mode not in CATCH_MODES:
        raise ValueError('bad catch_mode %r' % spec.catch_mode)
    omit = set()
    for feat_key in OMITTABLE:
        if not getattr(spec, feat_key):
            omit.update(FEATURE_BY_KEY[feat_key].modules)
    catch_action = _CATCH_ACTION[spec.catch_mode]
    if catch_action != 'off' and not spec.flee_text:
        omit.add('G')                            # drop the flee-text suppressor within a catch build
    # x-catch builds force module-N delta encoding (to fit the pad), matching build_patch.main().
    enc = 'delta' if spec.catch_mode.startswith('x-catch') else spec.norecall_enc
    opts = dict(menu=spec.options_menu, trade=spec.trade_evolution, scripted_vanilla=spec.scripted_vanilla,
                caught_marker=spec.caught_marker, page=spec.options_page,
                defaults={k: int(getattr(spec, 'row_' + k)) for k in bp._bo.ROW_ORDER})
    if not (opts['menu'] or opts['trade'] or opts['scripted_vanilla'] or opts['caught_marker']):
        opts = None                              # a build without the settings layer
    return dict(
        options=opts,
        profile='classic' if spec.no_timer else 'timed',
        hp_gate='strict' if spec.strict_ko_gate else 'slot6',
        exp_recipients='participants' if spec.participants else 'party',
        exp_scope=spec.battle_exp_scope,
        catch_exp='off' if spec.no_catch_exp else 'on',
        catch_action=catch_action,
        x_stage='divert',
        input_source=spec.input_source,
        omit=tuple(sorted(omit)),
        norecall_enc=enc,
    )


# ----- coherence / feasibility ------------------------------------------------------------------
# ----- target game ------------------------------------------------------------------------------
GAMES = {k: v['title'] for k, v in bp.GAME_INFO.items()}     # 'pikachu' / 'eevee' -> display title


def resolve_game(text: bytes | None, game: str | None = None):
    """(game, error). `game` = the game the user asked for (None = detect from `text`). A text.bin
    from the other game is an error; without a text (game-file-free checks) the default is Pikachu."""
    if game is not None and game not in GAMES:
        return None, "Unknown game %r (choose %s)." % (game, ' or '.join(sorted(GAMES)))
    found = bp.game_of_text(text) if text is not None else None
    if game and found and game != found:
        return None, ("This text.bin is from %s 1.0.2, but you chose %s. Pick the matching game, or "
                      "the code file from your %s update." % (GAMES[found], GAMES[game], GAMES[game]))
    return (game or found or 'pikachu'), None




def validate(spec: BuildSpec, text: bytes | None = None, game: str | None = None):
    """Return (errors, warnings). Errors hard-block a build; warnings inform. `text` (the carved
    .text) enables the code-space overflow check; omit it for pure game-file-free coherence. `game`
    = the target game ('pikachu'/'eevee'; None = detect from `text`)."""
    errors, warnings = [], []
    g, gerr = resolve_game(text, game)
    if gerr:
        return [gerr], []
    bp.set_game(g)
    # enum validity first (so spec_to_args below can't raise)
    if spec.catch_mode not in CATCH_MODES:
        return (["catch_mode must be one of %s (got %r)." % (', '.join(CATCH_MODES), spec.catch_mode)], [])
    if spec.input_source not in ('handheld', 'all'):
        errors.append("input_source must be 'handheld' or 'all'.")
    if spec.norecall_enc not in ('table', 'delta'):
        errors.append("norecall_enc must be 'table' or 'delta'.")
    if spec.battle_exp_scope not in ('wild', 'all'):
        errors.append("battle_exp_scope must be 'wild' or 'all'.")
    if errors:
        return errors, warnings
    args = spec_to_args(spec)
    omit = set(args['omit'])

    # --- hard errors -------------------------------------------------------------------------
    if 'M' in omit or not spec.core_marker:
        errors.append("The wild-encounter marker (core_marker/M) is load-bearing — without it nothing "
                      "is scoped to wild battles, so the build is rejected.")
    # a catch mode needs the after-battle redirect (E) to reach the native capture; X-CATCH also
    # needs the wild-reflect (R) for the live HP gate.
    if spec.catch_mode != 'none':
        if not spec.core_evolution:
            errors.append("The catch feature needs the after-battle module (core_evolution/E) to "
                          "route into the native capture — enable it or set catch to 'none'.")
        if spec.catch_mode.startswith('x-catch') and not spec.core_reflect:
            errors.append("X-CATCH needs wild-identity capture (core_reflect/R) for the HP gate — "
                          "enable it or choose a different catch mode.")
    # the strict KO gate reads the reflect capture.
    if spec.core_result and spec.strict_ko_gate and not spec.core_reflect:
        errors.append("The strict KO gate (core_result + strict) reads the wild-identity capture — "
                      "enable core_reflect/R or turn the strict gate off.")

    # code-space fit (needs the real .text)
    if text is not None and not errors:
        fit = layout_fit(spec, text, g)
        if fit['pad_over'] > 0:
            errors.append("This selection overflows the %d-byte code-cave pad by %d bytes "
                          "(used %d). Drop a feature (e.g. a catch mode, or switch module-N to its "
                          "delta encoding) to fit." % (PAD_SIZE, fit['pad_over'], fit['pad_used']))

    # --- warnings ---------------------------------------------------------------------------
    for fk, msg in (
        ('crash_guard', "Crash guards OFF — a post-faint crash is possible on some wild models, and wild battles crash before \"Play with Pikachu/Eevee\" is unlocked."),
        ('no_recall', "No-recall faint OFF — the ball-recall animation will play when a wild Pokémon faints."),
    ):
        if not getattr(spec, fk):
            warnings.append(msg)
    for fk in ('core_faint', 'core_result', 'core_exp'):
        if not getattr(spec, fk):
            warnings.append("Core feature '%s' is OFF — the result may not be a functional "
                            "wild-battles mod." % FEATURE_BY_KEY[fk].name)
    if not spec.participants:
        warnings.append("Participants-only EXP OFF — every battle awards the native party-wide EXP.")
    elif spec.battle_exp_scope == 'wild':
        warnings.append("Participants-only EXP is limited to WILD battles: Trainer "
                        "and scripted battles still award the native party-wide EXP.")
    if not spec.no_catch_exp:
        warnings.append("No-EXP-from-catching OFF — every catch awards the native party-wide EXP and "
                        "shows the EXP screen.")
    if spec.catch_mode == 'x-catch-anytime':
        warnings.append("X-CATCH 'anytime' lets you catch at ANY wild HP (no ≤25% gate) — a dev/debug knob.")
    return errors, warnings


@contextlib.contextmanager
def norecall_encoding(encoding):
    """build_patch.NORECALL_ENC = `encoding` for the duration (it is a module-wide setting)."""
    saved = bp.NORECALL_ENC
    bp.NORECALL_ENC = encoding
    try:
        yield
    finally:
        bp.NORECALL_ENC = saved


def _lay_out(text: bytes, a: dict, strict: bool = True) -> dict:
    """build_patch.lay_out for spec_to_args() output `a` (call inside norecall_encoding)."""
    return bp.lay_out(text, a['profile'], a['hp_gate'], a['exp_recipients'], a['catch_action'],
                      omit=a['omit'], input_source=a['input_source'], x_stage=a['x_stage'],
                      exp_scope=a['exp_scope'], catch_exp=a['catch_exp'], strict=strict,
                      options=a['options'])


def layout_fit(spec: BuildSpec, text: bytes, game: str | None = None) -> dict:
    """Dry-run the cave layout and report fit (never raises on overflow)."""
    g, gerr = resolve_game(text, game)
    if gerr:
        raise ValueError(gerr)
    bp.set_game(g)
    a = spec_to_args(spec)
    with norecall_encoding(a['norecall_enc']):
        caves = _lay_out(text, a, strict=False)
    tail = [c for c in caves.values() if c.get('region') is None and not c.get('inplace')]
    tail_end = max((c['base'] + len(c['words']) * 4 for c in tail), default=PAD_LO)
    return dict(
        caves=caves,
        pad_used=tail_end - PAD_LO,
        pad_free=max(0, PAD_HI - tail_end),
        pad_over=max(0, tail_end - PAD_HI),
    )


# ----- build driver -----------------------------------------------------------------------------
def _reproducible_zip(mod_name: str, pchtxt_path: Path, zip_path: Path):
    """Reproducible Eden-importable zip: <Mod>/exefs/<file>.pchtxt, fixed mtime, sorted, no extras —
    mirrors tools/package_release.sh so a zip built here matches the release zips."""
    import tempfile, shutil
    stage = Path(tempfile.mkdtemp())
    try:
        exefs = stage / mod_name / 'exefs'
        exefs.mkdir(parents=True)
        shutil.copy2(pchtxt_path, exefs / pchtxt_path.name)
        subprocess.run(['find', mod_name, '-exec', 'touch', '-t', '198001010000.00', '{}', '+'],
                       cwd=stage, check=True)
        files = sorted(p.relative_to(stage).as_posix() for p in (stage / mod_name).rglob('*') if p.is_file())
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        if zip_path.exists():
            zip_path.unlink()
        subprocess.run(['zip', '-X', '-D', '-q', str(zip_path), *files], cwd=stage, check=True)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


APP_VERSION = '1.0.0-beta'                  # the release version (the site and the zips use it too)
SELF_MOD_NAME = 'Wild Battles Remastered'     # the mod's folder name

def build(spec: BuildSpec, text: bytes, base_src_path: str | None, out_pchtxt: str,
          manifest: str | None = None, zip_path: str | None = None,
          mod_name: str | None = None, game: str | None = None) -> dict:
    g, gerr = resolve_game(text, game)
    if gerr:
        raise ValueError(gerr)
    bp.set_game(g)
    base_kind = 'external' if base_src_path else 'self'
    bp.BUDDY_FORCE = (base_kind == 'self')
    if mod_name is None:
        mod_name = SELF_MOD_NAME
    a = spec_to_args(spec)
    if base_kind == 'external':
        a['options'] = None                      # an external base has no room for the options layer
    bp.verify_preimages(text)
    with norecall_encoding(a['norecall_enc']):
        caves = _lay_out(text, a)
        bp.verify_caves(text, caves)
        bp.verify_scratch_use(caves)
        bp.verify_cave_padding(text, caves)
        base_recs, hdr = bp.unified_base(text, base_kind, base_src_path)
        merged = base_recs + bp.variant_records('main', caves) + bp.extra_records(caves)
        bp.verify_marker(merged)
        bp.overlap_check('mod_spec', merged)
        merged = bp.disjoint(merged)
        # Same base + header as build_patch's --out-unified path, so a default/product spec
        # reproduces the shipped file byte-for-byte (sha matches the committed manifest for that base).
        bp.emit(merged, out_pchtxt, hdr)
        if manifest:
            bp.write_manifest(manifest, a['profile'], caves, {'unified': (out_pchtxt, merged)},
                              a['hp_gate'], a['exp_recipients'], a['catch_action'],
                              exp_scope=a['exp_scope'], catch_exp=a['catch_exp'], base=base_kind)
    if zip_path:
        _reproducible_zip(mod_name, Path(out_pchtxt), Path(zip_path))
    fit = layout_fit(spec, text, g)
    sha = hashlib.sha256(Path(out_pchtxt).read_bytes()).hexdigest()
    return dict(
        out=out_pchtxt, zip=zip_path, records=len(merged), sha256=sha, base=base_kind, game=g,
        enabled=[f.key for f in FEATURES if _feature_on(spec, f)],
        catch_mode=spec.catch_mode, args=a,
        pad_used=fit['pad_used'], pad_free=fit['pad_free'],
        scratch_bytes=len(bp.used_scratch(caves)),
        caves={k: len(c['words']) * 4 for k, c in caves.items()},
    )


def _feature_on(spec: BuildSpec, f: Feature) -> bool:
    if f.key == 'flee_text':
        return spec.catch_mode != 'none' and spec.flee_text
    if hasattr(spec, f.key):
        return bool(getattr(spec, f.key))
    return False
