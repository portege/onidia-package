#!/usr/bin/env bash
# mkdeb.sh - build one Debian package for a single app.
#
# usage: mkdeb.sh --name NAME --version V --arch ARCH --maintainer "Who <mail>" \
#                 --description "one line" --section SECTION --homepage URL \
#                 --out DIR [--file SRC:DEST[:MODE]]... [--dir SRC:DST]... \
#                 [--link TARGET:DEST]... [--depends LIST] [--recommends LIST]
#
# --depends / --recommends take a comma-separated Debian dependency string,
# e.g. "python3 | python3-minimal, pipewire-bin | pipewire-utils".
# Debian's "|" alternatives syntax is honoured as-is, which matters: Raspberry
# Pi OS ships pw-record/pw-play in pipewire-bin while upstream Debian uses
# pipewire-utils, so naming only one breaks Depends: on the other.
#
# Layout it produces (nothing outside the listed paths):
#   opt/<name>/bin/...                 the binaries
#   usr/bin/<tool>                    symlink -> /opt/<name>/bin/<tool>
#   usr/share/applications/<name>.desktop
#   usr/share/icons/hicolor/scalable/apps/<name>.svg
#   usr/share/doc/<name>/README.md
# Result: DIR/<name>_<version>_<debarch>.deb
set -euo pipefail

NAME= VERSION= ARCH= MAINTAINER= DESCRIPTION= SECTION=utils HOMEPAGE= OUT=
DEPENDS= RECOMMENDS=
FILES=() DIRS=() LINKS=()
while [ $# -gt 0 ]; do
case "$1" in
--name) NAME=$2; shift 2 ;;
--version) VERSION=$2; shift 2 ;;
--arch) ARCH=$2; shift 2 ;;
--maintainer) MAINTAINER=$2; shift 2 ;;
--description) DESCRIPTION=$2; shift 2 ;;
--section) SECTION=$2; shift 2 ;;
--homepage) HOMEPAGE=$2; shift 2 ;;
--out) OUT=$2; shift 2 ;;
--file) FILES+=("$2"); shift 2 ;;
--dir) DIRS+=("$2"); shift 2 ;;
--link) LINKS+=("$2"); shift 2 ;;
--depends) DEPENDS="$2"; shift 2 ;;
--recommends) RECOMMENDS="$2"; shift 2 ;;
*) echo "mkdeb: unknown argument: $1" >&2; exit 2 ;;
esac
done
: "${NAME:?}" "${VERSION:?}" "${ARCH:?}" "${OUT:?}" "${MAINTAINER:?}" "${DESCRIPTION:?}"
[ ${#FILES[@]} -gt 0 ] || { echo "mkdeb: nothing to package (no --file/--dir)" >&2; exit 2; }
command -v dpkg-deb >/dev/null 2>&1 || { echo "mkdeb: dpkg-deb not found" >&2; exit 1; }

# uname names -> Debian architecture names. Note armel vs armhf is NOT a
# cosmetic split: Debian's armhf requires ARMv7 + VFPv3, so a GOARM=6 build
# (original Pi Zero / Pi 1, BCM2835) must claim armel or apt will hand it to a
# machine that cannot execute it.
case "$ARCH" in
aarch64 | arm64) DARCH=arm64 ;;
amd64 | x86_64) DARCH=amd64 ;;
armv7l | armhf) DARCH=armhf ;;
    armv6l | armel) DARCH=armel ;;
i386 | i686) DARCH=i386 ;;
*) echo "mkdeb: unmapped architecture: $ARCH" >&2; exit 2 ;;
esac

STAGE="$OUT/.stage-$NAME-$DARCH"
rm -rf "$STAGE"
mkdir -p "$STAGE"

for spec in "${FILES[@]}"; do
IFS=: read -r src dst mode <<< "$spec"
[ -e "$src" ] || { echo "mkdeb: missing source: $src" >&2; exit 2; }
mkdir -p "$STAGE/$(dirname "$dst")"
cp -a "$src" "$STAGE/$dst"
chmod "${mode:-0644}" "$STAGE/$dst"
done

for spec in "${DIRS[@]}"; do
IFS=: read -r src dst <<< "$spec"
[ -d "$src" ] || { echo "mkdeb: missing directory: $src" >&2; exit 2; }
mkdir -p "$STAGE/$dst"
cp -a "$src/." "$STAGE/$dst/"
chmod -R a+rX,go-w "$STAGE/$dst"
done

for spec in "${LINKS[@]}"; do
IFS=: read -r target dest <<< "$spec"
mkdir -p "$STAGE/$(dirname "$dest")"
ln -s "$target" "$STAGE/$dest"
done

SIZE=$(du -sk "$STAGE" | awk '{print $1}')
mkdir -p "$STAGE/DEBIAN"
{
echo "Package: $NAME"
echo "Version: $VERSION"
echo "Architecture: $DARCH"
echo "Maintainer: $MAINTAINER"
echo "Installed-Size: $SIZE"
echo "Section: $SECTION"
echo "Priority: optional"
[ -n "$HOMEPAGE" ] && echo "Homepage: $HOMEPAGE"
echo "Description: $DESCRIPTION"
# Depends is empty on purpose: the binaries are static (CGO_ENABLED=0,
# no cgo anywhere), so there is not even a libc dependency to name.
# Everything the apps touch at runtime - X11, audio players, a
# recorder, python for the agents - is a Recommends. Recommends installs
# by default but apt does not refuse the install if it cannot, which
# is the behaviour these apps want: they degrade (no speech, no mic)
# instead of refusing to start.
[ -n "$DEPENDS" ] && echo "Depends: $DEPENDS"
[ -n "$RECOMMENDS" ] && echo "Recommends: $RECOMMENDS"
} > "$STAGE/DEBIAN/control"
chmod 0644 "$STAGE/DEBIAN/control"

DEB="$OUT/${NAME}_${VERSION}_${DARCH}.deb"
dpkg-deb --root-owner-group -Zgzip --build "$STAGE" "$DEB" >/dev/null
rm -rf "$STAGE"
echo "mkdeb: $DEB"
