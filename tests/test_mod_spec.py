#!/usr/bin/env python3
"""Tests for the feature-selection core (tools/mod_spec.py): the build spec, how it maps to the
generator's flags, its coherence errors and warnings, and whether each choice fits the code-cave
page. With your own game files (WB_TEXT_BIN, and optionally WB_TEXT_BIN_EEVEE) it also checks that
the default spec rebuilds each game's committed mod byte for byte and that the Options build through
this module equals the command-line build.

Run: python3 -I tests/test_mod_spec.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import REPO_ROOT, TOOLS_DIR, check, fake_text, finish  # noqa: E402
import build_patch as bp  # noqa: E402
import mod_spec as ms  # noqa: E402

OPTION_DEFAULTS = dict(wild=1, quick=1, share=0, catch_exp=0, timer=0, marker=1, recall=0, trade=0)
EXPECTED_DEFAULT_ARGS = dict(
    profile='classic', hp_gate='strict', exp_recipients='participants', exp_scope='all', catch_exp='off',
    catch_action='x-lowhp', x_stage='divert', input_source='handheld', omit=(), norecall_enc='delta',
    options=dict(menu=True, trade=True, scripted_vanilla=True, caught_marker=True, page=True,
                 defaults=OPTION_DEFAULTS))
CATCH_MODE_ACTIONS = (('none', 'off'), ('quick-catch', 'zl-run'),
                      ('x-catch-lowhp', 'x-lowhp'), ('x-catch-anytime', 'x-anytime'))
GAME_MANIFESTS = {'pikachu': 'build-manifest.json', 'eevee': 'build-manifest.eevee.json'}


def default_spec(**changes):
    """The shipped default spec with some fields changed."""
    spec = ms.BuildSpec.shipped_default()
    for field, value in changes.items():
        setattr(spec, field, value)
    return spec


def options_spec():
    """The default spec with the Options layer and every extra turned on."""
    return default_spec(options_menu=True, trade_evolution=True, scripted_vanilla=True, caught_marker=True)


def errors_of(spec, text):
    return ms.validate(spec, text)[0]


def warnings_of(spec, text=None):
    return ms.validate(spec, text)[1]


def sha256_of_file(path):
    with open(path, 'rb') as file:
        return hashlib.sha256(file.read()).hexdigest()


def load_json(path):
    with open(path) as file:
        return json.load(file)


def test_json_round_trip():
    spec = default_spec()
    check('roundtrip', ms.BuildSpec.from_json(spec.to_json()) == spec)
    try:
        ms.BuildSpec.from_json('{"bogus": 1}')
        check('reject-unknown', False, 'accepted a bogus key')
    except ValueError:
        pass


def test_default_maps_to_product_flags():
    args = ms.spec_to_args(default_spec())
    check('default args', args == EXPECTED_DEFAULT_ARGS, 'got %s' % args)


def test_options_args():
    spec = default_spec(options_menu=True, trade_evolution=True, scripted_vanilla=True, caught_marker=True, row_trade=1)
    options = ms.spec_to_args(spec)['options']
    want = dict(menu=True, trade=True, scripted_vanilla=True, caught_marker=True, page=True,
                defaults=dict(OPTION_DEFAULTS, trade=1))
    check('options args', options == want, 'got %s' % options)
    check('options spec roundtrip', ms.BuildSpec.from_json(spec.to_json()) == spec)


def test_no_catch_exp_flag():
    """No EXP from catching (XF/XG) is on by default; turning it off maps to catch_exp='on' with a warning."""
    check('no_catch_exp default on', default_spec().no_catch_exp is True)
    catch_exp_on = default_spec(no_catch_exp=False)
    check('no_catch_exp off -> catch_exp on', ms.spec_to_args(catch_exp_on)['catch_exp'] == 'on')
    warns = any('catch' in warning.lower() and 'party-wide' in warning for warning in warnings_of(catch_exp_on))
    check('no_catch_exp off warns', warns)
    check('no_catch_exp default no warning',
          not any('No-EXP-from-catching' in warning for warning in warnings_of(default_spec())))


def test_catch_mode_mapping():
    """Catch modes map one to one and never select two module sets together."""
    for mode, action in CATCH_MODE_ACTIONS:
        check('catch map %s' % mode, ms.spec_to_args(default_spec(catch_mode=mode))['catch_action'] == action)


def test_coherence_errors(text):
    check('drop-marker blocks', any('load-bearing' in error for error in errors_of(default_spec(core_marker=False), text)))
    # X-CATCH needs the wild-HP reflect module, and any catch mode needs the after-battle module
    check('x-catch needs reflect', any('core_reflect' in error for error in errors_of(default_spec(core_reflect=False), text)))
    check('catch needs evolution',
          any('core_evolution' in error for error in errors_of(default_spec(core_evolution=False), text)))
    check('bad catch_mode blocks', bool(errors_of(default_spec(catch_mode='bogus'), text)))


def test_default_is_coherent_and_fits(text):
    spec = default_spec()
    errors = errors_of(spec, text)
    check('default coherent', not errors, 'errors: %s' % errors)
    fit = ms.layout_fit(spec, text)
    check('default fits', fit['pad_over'] == 0, 'over pad=%d' % fit['pad_over'])


def test_every_catch_mode_fits(text):
    for mode in ms.CATCH_MODES:
        spec = default_spec(catch_mode=mode)
        errors = errors_of(spec, text)
        fit = ms.layout_fit(spec, text)
        check('feasible %s' % mode, not errors and fit['pad_over'] == 0, 'errs=%s over=%d' % (errors, fit['pad_over']))


def test_risky_drops_warn(text):
    check('crash-guard warns', any('crash' in warning.lower() for warning in warnings_of(default_spec(crash_guard=False), text)))
    check('no-recall warns', any('recall' in warning.lower() for warning in warnings_of(default_spec(no_recall=False), text)))




def game_text_paths():
    """The text.bin files given for the byte-identity check (either game; the game is detected from it)."""
    pikachu_text = os.environ.get('WB_TEXT_BIN') or ''
    candidates = (pikachu_text, os.environ.get('WB_TEXT_BIN_EEVEE'))
    return [path for path in candidates if path and os.path.exists(path)]


def build_with_cli(text_path, out_path):
    """The Options build through the command line, for comparison with the build through mod_spec."""
    command = [sys.executable, '-I', os.path.join(TOOLS_DIR, 'build_patch.py'), '--text', text_path, '--base', 'self',
               '--options-menu', 'on', '--trade-evo', 'on', '--scripted-vanilla', 'on', '--caught-marker', 'on',
               '--out-unified', out_path]
    subprocess.run(command, check=True, capture_output=True)


def check_byte_identity(text_path, real_text, game, temp_dir):
    """The default spec's self-base build equals the game's committed mod; the Options spec builds the
    same bytes as the command line; the other game's text.bin is refused."""
    manifest_path = os.path.join(REPO_ROOT, GAME_MANIFESTS[game])
    summary = ms.build(default_spec(), real_text, None, os.path.join(temp_dir, 'default.pchtxt'))
    want = load_json(manifest_path)['outputs']['unified']['sha256']
    matches = summary['base'] == 'self' and summary['game'] == game and summary['sha256'] == want
    check('%s self base == shipped bytes' % game, matches,
          '%s %s %s != %s' % (summary['base'], summary['game'], summary['sha256'], want))

    spec = options_spec()
    errors = errors_of(spec, real_text)
    check('%s options spec coherent' % game, not errors, str(errors))
    app_summary = ms.build(spec, real_text, None, os.path.join(temp_dir, 'options.pchtxt'))
    cli_path = os.path.join(temp_dir, 'cli.pchtxt')
    build_with_cli(text_path, cli_path)
    check('%s options: app == CLI' % game, app_summary['sha256'] == sha256_of_file(cli_path))


    other_game = 'eevee' if game == 'pikachu' else 'pikachu'
    errors = ms.validate(default_spec(), real_text, other_game)[0]
    check('%s text.bin refused for %s' % (game, other_game), bool(errors) and 'you chose' in errors[0], str(errors))



def test_byte_identity():
    """Returns the games whose byte identity was checked."""
    checked = []
    try:
        for path in game_text_paths():
            with open(path, 'rb') as file:
                real_text = file.read()
            game = bp.game_of_text(real_text)
            if game is None or not os.path.isfile(os.path.join(REPO_ROOT, GAME_MANIFESTS.get(game, ''))):
                continue
            with tempfile.TemporaryDirectory() as temp_dir:
                check_byte_identity(path, real_text, game, temp_dir)
            checked.append(game)
    finally:
        bp.set_game('pikachu')
    if not checked:
        print('note: no text.bin (set WB_TEXT_BIN) — skipping the byte-identity check')
    return checked


def main():
    text = fake_text()
    test_json_round_trip()
    test_default_maps_to_product_flags()
    test_options_args()
    test_no_catch_exp_flag()
    test_catch_mode_mapping()
    test_coherence_errors(text)
    test_default_is_coherent_and_fits(text)
    test_every_catch_mode_fits(text)
    test_risky_drops_warn(text)
    games = test_byte_identity()
    summary = 'spec/coherence/fit; byte identity: %s' % (', '.join(games) if games else 'skipped')
    finish('mod_spec tests passed (%s)' % summary)


if __name__ == '__main__':
    main()
