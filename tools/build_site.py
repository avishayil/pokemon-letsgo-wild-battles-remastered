#!/usr/bin/env python3
"""Build the project website (GitHub Pages) from site/ into _site/.

The site is plain HTML/CSS/JS. This script copies it, expands the shared page parts and fills the
{{PLACEHOLDERS}} from one source of truth, so the version and download links can never drift from the
release:
  VERSION        tools/mod_spec.py APP_VERSION (read as text; no capstone needed)
  ZIP_NAME/URL   the versioned Let's Go, Pikachu! mod zip on that release
  ZIP_EEVEE_*    the versioned Let's Go, Eevee! mod zip on that release
  ZIP_*ATMO_URL  the Atmosphere (real Switch) zips on that same release (versioned links: GitHub's
                 releases/latest skips prereleases, so latest/download 404s for a beta)

Shared parts live once in site/partials/<name>.html and are pulled into a page with {{> name}}. An
include can name the current page, {{> header active=install}}: inside the part, the link marked
data-nav="install" then gets aria-current="page". The partials folder is not part of the built site.

It fails if any placeholder or include is left unresolved, and --check-links requests every generated
URL (downloads, release page, repo) and fails on a non-2xx/3xx answer.

  python3 -I tools/build_site.py [--out _site] [--check-links]
"""
import argparse
import hashlib
import re
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = 'avishayil/pokemon-letsgo-wild-battles-remastered'
REPO_URL = 'https://github.com/' + REPO
SITE_URL = 'https://avishay.co.il/pokemon-letsgo-wild-battles-remastered/'   # Pages via the account's custom domain
TEXT_EXT = {'.html', '.css', '.js', '.txt', '.xml', '.webmanifest', '.svg'}
PARTIALS_DIR = 'partials'
CACHE_BUSTED_FILES = ('styles.css', 'site.js')
FINGERPRINT_LENGTH = 10                  # hex digits of the content hash in "styles.css?v=<hash>"
LINK_CHECK_TIMEOUT_SECONDS = 30
FIRST_HTTP_ERROR_STATUS = 400

PLACEHOLDER = re.compile(r'\{\{([A-Z_]+)\}\}')
INCLUDE = re.compile(r'\{\{>\s*([a-z][a-z0-9-]*)(?:\s+active=([a-z][a-z0-9-]*))?\s*\}\}')
NAV_MARKER = re.compile(r' data-nav="([a-z][a-z0-9-]*)"')
UNRESOLVED = re.compile(r'\{\{[^}]*\}\}')


def app_version():
    match = re.search(r"^APP_VERSION\s*=\s*'([^']+)'", (ROOT / 'tools' / 'mod_spec.py').read_text(), re.M)
    if not match:
        sys.exit('APP_VERSION not found in tools/mod_spec.py')
    return match.group(1)


def placeholder_values():
    version = app_version()
    tag = 'v' + version
    download_base = '%s/releases/download/%s/' % (REPO_URL, tag)
    zip_name = 'WildBattlesRemastered-%s.zip' % tag
    eevee_zip_name = 'WildBattlesRemastered-Eevee-%s.zip' % tag
    return {
        'VERSION': version,
        'ZIP_NAME': zip_name,
        'ZIP_URL': download_base + zip_name,
        'ZIP_EEVEE_NAME': eevee_zip_name,
        'ZIP_EEVEE_URL': download_base + eevee_zip_name,
        'RELEASE_URL': '%s/releases/tag/%s' % (REPO_URL, tag),
        'RELEASES_URL': REPO_URL + '/releases',
        'REPO_URL': REPO_URL,
        'SITE_URL': SITE_URL,
        'ZIP_ATMO_URL': download_base + 'WildBattlesRemastered-Atmosphere-%s.zip' % tag,
        'ZIP_EEVEE_ATMO_URL': download_base + 'WildBattlesRemastered-Eevee-Atmosphere-%s.zip' % tag,
    }


def source_dir():
    source = ROOT / 'site'
    if not source.is_dir():
        sys.exit('no site source directory at %s' % source)
    return source


def load_partials(source):
    return {path.stem: path.read_text() for path in sorted((source / PARTIALS_DIR).glob('*.html'))}


def mark_active_link(partial, active_page):
    """Turn the data-nav marker of the active page into aria-current and drop all the others."""
    def replace_marker(match):
        return ' aria-current="page"' if match.group(1) == active_page else ''
    return NAV_MARKER.sub(replace_marker, partial)


def expand_includes(page, partials, page_name):
    def replace_include(match):
        name, active_page = match.groups()
        if name not in partials:
            sys.exit('%s: unknown partial {{> %s}}' % (page_name, name))
        return mark_active_link(partials[name], active_page).rstrip('\n')
    return INCLUDE.sub(replace_include, page)


def fill_placeholders(text, values):
    return PLACEHOLDER.sub(lambda match: values.get(match.group(1), match.group(0)), text)


def build(out):
    source, values = source_dir(), placeholder_values()
    partials = load_partials(source)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(source, out, ignore=shutil.ignore_patterns(PARTIALS_DIR))
    unresolved = []
    for path in sorted(out.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in TEXT_EXT:
            continue
        text = path.read_text()
        built = text
        if path.suffix.lower() == '.html':
            built = expand_includes(built, partials, path.name)
        built = fill_placeholders(built, values)
        relative_path = path.relative_to(out)
        unresolved += ['%s: %s' % (relative_path, tag) for tag in sorted(set(UNRESOLVED.findall(built)))]
        if built != text:
            path.write_text(built)
    (out / '.nojekyll').write_text('')                     # serve files as-is (no Jekyll processing)
    bust_caches(out)
    if unresolved:
        sys.exit('unresolved placeholders:\n  ' + '\n  '.join(unresolved))
    return values


def bust_caches(out):
    """Cache-busting: point every page at styles.css / site.js with a content fingerprint, so a
    browser holding an older copy fetches the new one as soon as it changes (Pages caches ~10 min)."""
    for name in CACHE_BUSTED_FILES:
        asset = out / name
        if not asset.exists():
            continue
        fingerprint = hashlib.sha256(asset.read_bytes()).hexdigest()[:FINGERPRINT_LENGTH]
        reference = re.compile(r'((?:href|src)=")%s"' % re.escape(name))
        for page in out.glob('*.html'):
            text = page.read_text()
            busted = reference.sub(r'\g<1>%s?v=%s"' % (name, fingerprint), text)
            if busted != text:
                page.write_text(busted)


def http_status(url):
    """The HTTP status of a HEAD request, or the error text when there is no HTTP answer."""
    request = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'build_site.py'})
    try:
        with urllib.request.urlopen(request, timeout=LINK_CHECK_TIMEOUT_SECONDS) as response:
            return response.status
    except Exception as error:                             # HTTPError has .code; others are network errors
        return getattr(error, 'code', str(error))


def check_links(values):
    urls = sorted({url for key, url in values.items() if key.endswith('_URL') and key != 'SITE_URL'})
    failed = []
    for url in urls:
        status = http_status(url)
        reachable = isinstance(status, int) and status < FIRST_HTTP_ERROR_STATUS
        if reachable:
            print('  ok  %s' % url)
        else:
            print('  BAD %s  (%s)' % (url, status))
            failed.append(url)
    if failed:
        sys.exit('%d link(s) failed' % len(failed))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--out', default=str(ROOT / '_site'))
    parser.add_argument('--check-links', action='store_true', help='HEAD-request every generated download/repo URL')
    args = parser.parse_args()
    out = Path(args.out)
    values = build(out)
    file_count = sum(1 for path in out.rglob('*') if path.is_file())
    print('built %s (%d files, version %s)' % (args.out, file_count, values['VERSION']))
    if args.check_links:
        check_links(values)


if __name__ == '__main__':
    main()
