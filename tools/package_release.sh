#!/usr/bin/env bash
# Package the mod as one Eden-importable zip (zip root = "<Mod Name>/exefs/<file>.pchtxt", which Eden's
# "Import Mod from ZIP" detects via its exefs scan), plus an Atmosphere zip for a real Switch. The
# .pchtxt is rebuilt fresh from build_patch.py with the product defaults and must match the COMMITTED
# manifest for that game byte-for-byte, so a release can never drift from the pinned bytes.
#
#   tools/package_release.sh <version> --text <text.bin> [--game pikachu|eevee]
#
#   Builds "Wild Battles Remastered" from the built-in base for one game and checks it against that
#   game's committed manifest: Pikachu (default) -> build-manifest.json and
#   WildBattlesRemastered-<version>.zip; Eevee -> build-manifest.eevee.json and
#   WildBattlesRemastered-Eevee-<version>.zip. Needs only that game's text.bin (1.0.2).
#
# FAIL-CLOSED: requires your own legally-dumped game .text (text.bin) with the matching NSO build-id;
# it never ships an empty/partial release. Reproducible zips (sorted entries, fixed mtimes, no extra
# attributes). Publishes no game code, keys or saves.
set -euo pipefail

readonly EXIT_USAGE=2
readonly EXIT_BAD_INPUT=3
readonly EXIT_BUILD_ID_MISMATCH=4
readonly EXIT_NO_ZIP=5
readonly ZIP_EPOCH="198001010000.00"   # touch -t stamp for every zip entry: 1980-01-01, the zip format's earliest date

USAGE="usage: package_release.sh <version> --text <text.bin> [--game pikachu|eevee]"

usage() {
  echo "$USAGE" >&2
  exit "$EXIT_USAGE"
}

# fail <exit code> <message line>...
fail() {
  local exit_code="$1"
  shift
  echo "ERROR: $1" >&2
  shift
  local detail_line
  for detail_line in "$@"; do
    echo "       $detail_line" >&2
  done
  exit "$exit_code"
}

# read_build_id <pchtxt>: the hex build id from the file's first line, "@nsobid-<build id>".
read_build_id() {
  local first_line=""
  IFS= read -r first_line < "$1" || true
  case "$first_line" in
    @nsobid-*) echo "${first_line#@nsobid-}" ;;
    *) echo "" ;;
  esac
}

# make_reproducible_zip <output zip> <source file> <path inside the zip>
# Zips one file under its path with every mtime set to ZIP_EPOCH, entries in byte order and no extra
# attributes or directory entries, so the same input always gives the same zip bytes.
make_reproducible_zip() {
  local output_zip="$1" source_file="$2" entry_path="$3"
  local top_dir="${entry_path%%/*}"
  local stage_dir
  stage_dir="$(mktemp -d "$WORK_DIR/zip-stage.XXXXXX")"
  mkdir -p "$stage_dir/$(dirname "$entry_path")"
  cp "$source_file" "$stage_dir/$entry_path"
  rm -f "$output_zip"                     # zip adds to an existing archive; always start fresh
  (
    cd "$stage_dir"
    find "$top_dir" -exec touch -t "$ZIP_EPOCH" {} +
    find "$top_dir" -type f | LC_ALL=C sort | zip -X -D -q "$output_zip" -@
  )
  [ -f "$output_zip" ] || fail "$EXIT_NO_ZIP" "release zip $output_zip was not produced."
}

# ---- arguments ----------------------------------------------------------------------------------
VERSION="${1:-}"
if [ -z "$VERSION" ] || [ "${VERSION#-}" != "$VERSION" ]; then
  usage
fi
shift
GAME=pikachu
TEXT_BIN=""
BASE_KIND=self
while [ $# -gt 0 ]; do
  if [ $# -lt 2 ] || [ -z "$2" ]; then
    usage                                   # every option takes a value
  fi
  case "$1" in
    --game) GAME="$2" ;;
    --text) TEXT_BIN="$2" ;;
    *) usage ;;
  esac
  shift 2
done

# ---- what to build ------------------------------------------------------------------------------
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="$ROOT/dist/$VERSION"
case "$GAME" in
  pikachu)
    EXPECTED_BUILD_ID="C208DB6A4EF4361F28B8402BE0F7CD9A"
    MOD_FOLDER_PREFIX=""                    # mods/<prefix><mod name>/exefs/
    ZIP_GAME_TAG=""                         # WildBattlesRemastered-<tag><version>.zip
    OUTPUT_SUFFIX=""                        # manifest<suffix>.json, build-info<suffix>.txt
    MANIFEST="build-manifest.json"
    ;;
  eevee)
    EXPECTED_BUILD_ID="5831EC64D6B696FD69E69505821B97B1"
    MOD_FOLDER_PREFIX="Eevee/"
    ZIP_GAME_TAG="Eevee-"
    OUTPUT_SUFFIX="-eevee"
    MANIFEST="build-manifest.eevee.json"
    ;;
  *) usage ;;
esac
MOD_NAME="Wild Battles Remastered"
PATCH_FILE="wild_battles_remastered.pchtxt"
EDEN_ZIP_NAME="WildBattlesRemastered-$ZIP_GAME_TAG$VERSION.zip"
ATMOSPHERE_ZIP_NAME="WildBattlesRemastered-${ZIP_GAME_TAG}Atmosphere-$VERSION.zip"
BUILD_PATCH_BASE_ARGS=(--base self)
WITH_ATMOSPHERE_ZIP=true

if [ -z "$TEXT_BIN" ] || [ ! -f "$TEXT_BIN" ]; then
  fail "$EXIT_BAD_INPUT" "--text <text.bin> is required (the game's carved .text from your own legal v1.0.2 dump)." \
    "Refusing to ship an empty release."
fi
[ -f "$ROOT/$MANIFEST" ] || fail "$EXIT_BAD_INPUT" "committed manifest $MANIFEST not found."

MOD_PATCH_PATH="mods/$MOD_FOLDER_PREFIX$MOD_NAME/exefs/$PATCH_FILE"   # relative to ROOT
EDEN_ZIP="$DIST_DIR/$EDEN_ZIP_NAME"
ATMOSPHERE_ZIP="$DIST_DIR/$ATMOSPHERE_ZIP_NAME"
RELEASE_MANIFEST="$DIST_DIR/manifest$OUTPUT_SUFFIX.json"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
IPS_PATCH="$WORK_DIR/patch.ips"

# ---- build and gate -----------------------------------------------------------------------------
mkdir -p "$ROOT/$(dirname "$MOD_PATCH_PATH")" "$DIST_DIR"
rm -f "$EDEN_ZIP" "$RELEASE_MANIFEST"       # only this game's files: the other game's zips share the dir

# Rebuild the product (default flags) into its committed path and gate it against the committed manifest.
# The same run writes the IPS32 copy for Atmosphere (real hardware) from the same verified records.
BUILD_PATCH_OUTPUT_ARGS=(--out-unified "$MOD_PATCH_PATH")
if [ "$WITH_ATMOSPHERE_ZIP" = true ]; then
  BUILD_PATCH_OUTPUT_ARGS+=(--out-ips32 "$IPS_PATCH")
fi
( cd "$ROOT" && python3 -I tools/build_patch.py --text "$TEXT_BIN" --game "$GAME" "${BUILD_PATCH_BASE_ARGS[@]}" \
    "${BUILD_PATCH_OUTPUT_ARGS[@]}" --manifest "$RELEASE_MANIFEST" --verify-manifest "$MANIFEST" >/dev/null )

BUILT_BUILD_ID="$(read_build_id "$ROOT/$MOD_PATCH_PATH")"
case "$BUILT_BUILD_ID" in
  "$EXPECTED_BUILD_ID"*) ;;
  *) fail "$EXIT_BUILD_ID_MISMATCH" "build-id $BUILT_BUILD_ID does not match expected $EXPECTED_BUILD_ID… — refusing." ;;
esac

# ---- zips ---------------------------------------------------------------------------------------
make_reproducible_zip "$EDEN_ZIP" "$ROOT/$MOD_PATCH_PATH" "$MOD_NAME/exefs/$PATCH_FILE"
if [ "$WITH_ATMOSPHERE_ZIP" = true ]; then
  # Atmosphere (real hardware) layout: atmosphere/exefs_patches/<mod>/<build-id>.ips
  make_reproducible_zip "$ATMOSPHERE_ZIP" "$IPS_PATCH" \
    "atmosphere/exefs_patches/$MOD_NAME/$EXPECTED_BUILD_ID.ips"
fi

# Tool-version record (these tools influence the emitted bytes).
ZIP_TOOL_VERSION="$(zip -v 2>/dev/null | sed -n '2p')"   # line 2 of "zip -v" names the version
{
  echo "version: $VERSION"
  echo "game: $GAME"
  echo "base: $BASE_KIND"
  echo "python: $(python3 --version 2>&1)"
  echo "zip: $ZIP_TOOL_VERSION"
} > "$DIST_DIR/build-info$OUTPUT_SUFFIX.txt"

( cd "$DIST_DIR" && shasum -a 256 -- *.zip > SHA256SUMS && cat SHA256SUMS )
echo "== $EDEN_ZIP"
unzip -l "$EDEN_ZIP" | sed -n '4,8p'         # the entry rows of the listing, without its header
echo "OK: packaged $MOD_NAME for $GAME ($BASE_KIND base) -> $DIST_DIR"
