#!/usr/bin/env python3
"""Lint the committed mods/ against the committed build manifests, without game files.

Catches the drifts CI can see without rebuilding from the game's code:
  * a committed .pchtxt whose bytes or record count no longer match its manifest entry (a stale
    `mods/` next to a regenerated manifest, or a hand edit), and
  * a malformed patch file (bad header or footer, bad record line, address outside the patchable
    window).

Run: python3 -I tests/lint_mods.py   (exit 0 = clean, 1 = problems printed)
"""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import REPO_ROOT, check, finish  # noqa: E402
import build_patch as bp  # noqa: E402

ROOT = Path(REPO_ROOT)
MODS = ROOT / 'mods'
MAIN_MANIFEST = ROOT / 'build-manifest.json'      # required
MANIFESTS = [MAIN_MANIFEST,
             ROOT / 'build-manifest.eevee.json']   # the Let's Go, Eevee! product, if present

# Patchable window in pchtxt space (memory offset + 0x100): .text plus the code-cave page.
ADDRESS_LOW = bp.PCHTXT_OFF
ADDRESS_HIGH = bp.CAVE_END_PAGE + bp.PCHTXT_OFF
# The Y catch and the settings layer also re-point single .rodata jump-table entries (the id-5
# command-menu entry and the trade-evolution entry). Those are one-word data edits, not code-cave
# writes, so their addresses are allowed too.
RODATA_ENTRIES_OK = {bp.RODATA_ID5_ENTRY[0] + bp.PCHTXT_OFF, bp._bo.EVO_ENTRY5 + bp.PCHTXT_OFF}
HEADER_LINE = re.compile(r'^@nsobid-[0-9A-Fa-f]{64}$')
RECORD_LINE = re.compile(r'^[0-9A-F]{8} (?:[0-9A-F]{2})+$')


def address_allowed(address):
    return ADDRESS_LOW <= address < ADDRESS_HIGH or address in RODATA_ENTRIES_OK


def lint_pchtxt(path):
    """Check one patch file's structure and record addresses; returns its record count."""
    name = path.name
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    check('%s: first line is @nsobid-<64hex>' % name, bool(lines) and bool(HEADER_LINE.match(lines[0])))
    check('%s: has @enabled' % name, '@enabled' in lines)
    check('%s: ends with @stop' % name, bool(lines) and lines[-1] == '@stop')
    record_count = 0
    for line in lines:
        if line.startswith('@') or line.startswith('//'):
            continue
        if not check('%s: record line well-formed' % name, bool(RECORD_LINE.match(line)), repr(line)):
            continue
        record_count += 1
        address = int(line[:8], 16)
        check('%s: record address inside .text+cave window' % name, address_allowed(address), '0x%08X' % address)
    return record_count




def check_manifest_outputs(manifest_name, manifest, record_counts):
    """Each output's committed file exists and matches the manifest's sha256 and record count.
    Returns how many outputs were checked."""
    checked = 0
    for output_name, output in manifest.get('outputs', {}).items():
        if output_name == 'ips32':
            continue            # the Atmosphere copy ships in its release zip, not in the repo
        checked += 1
        label = '%s output %s' % (manifest_name, output_name)
        path = ROOT / output['path']
        if not check('%s: file exists' % label, path.exists(), output['path']):
            continue
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        check('%s: sha256 matches' % label, got == output['sha256'], '\n    file %s\n    man  %s' % (got, output['sha256']))
        relative = str(path.relative_to(ROOT))
        if relative in record_counts:
            check('%s: record count matches' % label, record_counts[relative] == output['records'],
                  '%d != manifest %d' % (record_counts[relative], output['records']))
    return checked


def main():
    record_counts = {str(path.relative_to(ROOT)): lint_pchtxt(path) for path in sorted(MODS.rglob('*.pchtxt'))}
    check('%s exists' % MAIN_MANIFEST.name, MAIN_MANIFEST.exists())
    manifests = [path for path in MANIFESTS if path.exists()]
    hash_count = sum(check_manifest_outputs(path.name, json.loads(path.read_text()), record_counts)
                     for path in manifests)
    summary = ['%d pchtxt file(s) lint clean' % len(record_counts),
               '%d manifest hash(es) match across %d manifest(s)' % (hash_count, len(manifests))]
    finish(', '.join(summary))


if __name__ == '__main__':
    main()
