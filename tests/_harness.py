"""Shared helpers for the test scripts: the tools/ import path, a synthetic game image, and a
check recorder that collects every failure before reporting.

Each test file is a plain script. It calls check() for every expectation (a failed check is
recorded, never raised, so one run reports all problems) and ends with finish(), which prints
"ok — <summary>" or the list of failures and exits non-zero.
"""
import os
import struct
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
TOOLS_DIR = os.path.join(REPO_ROOT, 'tools')
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

# Scratch-page slots the generated caves use, under the names the tests match on. The caves address
# a slot either as [page + SCRATCH_PAGE_BIAS + slot] or, after adding the bias to the page register
# once, as [x16 + slot]; the tests match the exact encoded instruction, so they need both forms.
import build_patch as _bp  # noqa: E402

SCRATCH_PAGE_BIAS = _bp.SCRATCH_IN_PAGE
BUTTONS_MIRROR_SLOT = _bp.SLOT_BUTTONS
X_UNLOCK_SLOT = _bp.SLOT_XCATCH_UNLOCK
CATCH_INTENT_SLOT = _bp.SLOT_CATCH_INTENT
WILD_ENCOUNTER_SLOT = _bp.SLOT_WILD_ENCOUNTER

_failures = []
_checks_run = 0


def check(name, condition, detail=''):
    global _checks_run
    _checks_run += 1
    if not condition:
        _failures.append('%s: %s' % (name, detail) if detail else name)
    return bool(condition)


def checks_run():
    """How many checks have been recorded so far (passed or failed)."""
    return _checks_run


def finish(summary):
    """Print the recorded failures and exit 1, or print 'ok — <summary>' and return."""
    if _failures:
        print('FAIL — %d of %d check(s) failed:' % (len(_failures), _checks_run))
        for failure in _failures:
            print('  FAIL', failure)
        sys.exit(1)
    print('ok — %s' % summary)


def fake_text():
    """A synthetic code image: zero-filled up to the end of the code-cave page, with each hooked
    offset holding the instruction the generator expects there, and the base mod's marker word at
    its hook (the generator checks for it before it emits anything)."""
    import build_patch as bp
    image = bytearray(bp.CAVE_END_PAGE)
    for hook, preimage in bp.PREIMAGE.items():
        struct.pack_into('<I', image, hook, preimage)
    struct.pack_into('<I', image, bp.HOOK_M, bp.MARKER_WORD)
    return bytes(image)


def cave_end(caves):
    """The first byte past the highest cave in a lay_out() result."""
    return max(cave['base'] + 4 * len(cave['words']) for cave in caves.values())
