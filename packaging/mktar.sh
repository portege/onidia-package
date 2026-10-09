#!/usr/bin/env bash
# mktar.sh - assemble one release tarball for a single app.
#
# usage: mktar.sh --name NAME --version V --arch ARCH --out DIR \
#                 [--file SRC:DEST[:MODE]]... [--dir SRC:DST]...
#
# SRC paths are relative to the package directory (the make CWD); DEST is
# relative to the tarball root. Result:
#   dist/<name>-linux-<arch>-<version>.tar.gz
#   ├── bin/ ... share/ ... install.sh
#   └── SHA256SUMS          (verifiable with: sha256sum -c SHA256SUMS)
set -euo pipefail

NAME= VERSION= ARCH= OUT=
FILES=() DIRS=()
while [ $# -gt 0 ]; do
case "$1" in
--name) NAME=$2; shift 2 ;;
--version) VERSION=$2; shift 2 ;;
--arch) ARCH=$2; shift 2 ;;
--out) OUT=$2; shift 2 ;;
--file) FILES+=("$2"); shift 2 ;;
--dir) DIRS+=("$2"); shift 2 ;;
*) echo "mktar: unknown argument: $1" >&2; exit 2 ;;
esac
done
[ -n "$NAME" ] && [ -n "$VERSION" ] && [ -n "$ARCH" ] && [ -n "$OUT" ] || {
echo "mktar: --name, --version, --arch and --out are required" >&2; exit 2; }
[ ${#FILES[@]} -gt 0 ] || { echo "mktar: nothing to package (no --file/--dir)" >&2; exit 2; }

ROOT="$NAME-linux-$ARCH-$VERSION"
STAGE="$OUT/$ROOT"
rm -rf "$STAGE"
mkdir -p "$STAGE"

for spec in "${FILES[@]}"; do
IFS=: read -r src dst mode <<< "$spec"
[ -e "$src" ] || { echo "mktar: missing source: $src" >&2; exit 2; }
mkdir -p "$STAGE/$(dirname "$dst")"
cp -a "$src" "$STAGE/$dst"
[ -n "$mode" ] && chmod "$mode" "$STAGE/$dst"
done

for spec in "${DIRS[@]}"; do
IFS=: read -r src dst <<< "$spec"
[ -d "$src" ] || { echo "mktar: missing directory: $src" >&2; exit 2; }
mkdir -p "$STAGE/$dst"
cp -a "$src/." "$STAGE/$dst/"
chmod -R a+rX,go-w "$STAGE/$dst"
done

# checksums, so a hand-downloaded tarball can be verified after unpacking
( cd "$STAGE" && find . -type f ! -name SHA256SUMS | sort | xargs sha256sum > SHA256SUMS )

tar -C "$OUT" -czf "$OUT/$ROOT.tar.gz" "$ROOT"
rm -rf "$STAGE"
echo "mktar: $OUT/$ROOT.tar.gz"
