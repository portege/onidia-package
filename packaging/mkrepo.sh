#!/usr/bin/env bash
# mkrepo.sh - assemble (and sign) a self-hostable apt repository from built .debs.
#
# usage: mkrepo.sh [DIST_DEB_DIR] [REPO_OUT] [--sign]
#
#   DIST_DEB_DIR  directory of *.deb to publish   (default dist/deb)
#   REPO_OUT      repository root, served over HTTP (default dist/apt)
#   --sign        require a GPG signature; without it the repo is signed
#                 automatically when a secret key is available, and left
#                 unsigned (with a warning) when there is none.
#
# Layout (standard Debian pool + dists, so plain apt understands it):
#   <REPO_OUT>/pool/main/<first letter>/<pkg>/<pkg>_<v>_<arch>.deb
#   <REPO_OUT>/dists/stable/main/binary-<arch>/{Packages,Packages.gz,Release}
#   <REPO_OUT>/dists/stable/{Release,InRelease,Release.gpg}
#   <REPO_OUT>/portege-archive.key   public key for apt's signed-by=
#   <REPO_OUT>/add-repo.sh           one-liner installer for end users
set -euo pipefail

DEBDIR="${1:-dist/deb}"
OUT="${2:-dist/apt}"
MODE="${3:-}"

command -v apt-ftparchive >/dev/null 2>&1 || { echo "mkrepo: apt-ftparchive not found (apt-utils)" >&2; exit 1; }

mkdir -p "$DEBDIR" "$OUT"
DEBDIR="$(cd "$DEBDIR" && pwd)"
OUT="$(cd "$OUT" && pwd)"
DIST="$OUT/dists/stable"

shopt -s nullglob
debs=("$DEBDIR"/*.deb)
shopt -u nullglob
[ ${#debs[@]} -gt 0 ] || { echo "mkrepo: no .deb files in $DEBDIR (run 'make deb' first)" >&2; exit 1; }

# 1. pool/ - one copy per package, named the way apt expects to find it
POOL="$OUT/pool/main"
ARCHS=()
for deb in "${debs[@]}"; do
	name=$(dpkg-deb -f "$deb" Package)
	arch=$(dpkg-deb -f "$deb" Architecture)
	mkdir -p "$POOL/${name:0:1}/$name"
	cp -f "$deb" "$POOL/${name:0:1}/$name/"
	case " ${ARCHS[*]:-} " in
		*" $arch "*) ;;
		*) ARCHS+=("$arch") ;;
	esac
done
echo "mkrepo: ${#debs[@]} package(s), architectures: ${ARCHS[*]}"

# 2. per-architecture Packages index + a small Release for that index
for arch in "${ARCHS[@]}"; do
	bin="$DIST/main/binary-$arch"
	mkdir -p "$bin"
	( cd "$OUT" && apt-ftparchive --arch "$arch" packages pool ) > "$bin/Packages"
	gzip -9kf "$bin/Packages"
	{
		echo "Archive: stable"
		echo "Component: main"
		echo "Origin: Portege"
		echo "Label: Onidia desktop apps"
		echo "Architecture: $arch"
		echo "SHA256:"
		for f in Packages Packages.gz; do
				printf ' %s %16d %s\n' "$(sha256sum "$bin/$f" | cut -d' ' -f1)" "$(stat -c%s "$bin/$f")" "$f"
		done
	} > "$bin/Release"
done

# 3. top-level Release (apt-ftparchive adds the per-file checksums itself)
ARCHLIST="${ARCHS[*]}"
rm -f "$DIST/Release" "$DIST/InRelease" "$DIST/Release.gpg"
( cd "$DIST" && apt-ftparchive \
	-o APT::FTPArchive::Release::Origin="Portege" \
	-o APT::FTPArchive::Release::Label="Onidia desktop apps" \
	-o APT::FTPArchive::Release::Suite="stable" \
	-o APT::FTPArchive::Release::Codename="stable" \
	-o APT::FTPArchive::Release::Architectures="$ARCHLIST" \
	-o APT::FTPArchive::Release::Components="main" \
	-o APT::FTPArchive::Release::Description="Self-hosted packages for onidia and onidia-chat" \
	release . ) > "$DIST/Release"

# 4. signing
key_present() {
	# gpg --list-secret-keys exits 0 even on an empty keyring, so the exit status
	# alone would claim a key exists and the signing step would fail later -
	# look for an actual key record instead.
	local out
	if [ -n "${GPG_KEY:-}" ]; then
		out=$(gpg --batch --with-colons --list-secret-keys "$GPG_KEY" 2>/dev/null) || return 1
	else
		out=$(gpg --batch --with-colons --list-secret-keys 2>/dev/null) || return 1
	fi
	printf '%s\n' "$out" | grep -q '^sec:'
}

sign_release() {
	local args=(--batch --yes)
	[ -n "${GPG_KEY:-}" ] && args+=(-u "$GPG_KEY")
	( cd "$DIST" && gpg "${args[@]}" --armor --detach-sign -o Release.gpg Release )
	( cd "$DIST" && gpg "${args[@]}" --clearsign -o InRelease Release )
	gpg "${args[@]}" --armor --export > "$OUT/portege-archive.key"
	echo "mkrepo: signed (key: ${GPG_KEY:-default})"
}

if [ "$MODE" = "--sign" ]; then
	key_present || { echo "mkrepo: --sign given but no GPG secret key found (set GPG_KEY=<keyid>)" >&2; exit 1; }
	sign_release
elif key_present; then
	sign_release
else
	# a key file left by an earlier signed run could not verify this unsigned
	# Release, so drop it rather than publish a key that means nothing
	rm -f "$OUT/portege-archive.key"
	echo "mkrepo: no GPG secret key found - repository left UNSIGNED."
	echo "         apt needs '[trusted=yes]' until you create one:  gpg --full-generate-key"
fi

# 5. the user-facing one-liner installer
cat > "$OUT/add-repo.sh" <<'ADDREPO'
#!/bin/sh
# add-repo.sh - add the Portege apt repository to this machine, then refresh apt.
#
#   sudo ./add-repo.sh http://your-server/apt
#
# Serve this script from the repository root (or curl it from there). The URL
# is where the whole repository (pool/ + dists/) is published, no trailing slash.
set -eu

URL="${1:?usage: sudo $0 http://your-server/apt}"
case "$URL" in
	http://* | https://*) ;;
	*) echo "URL must start with http:// or https://" >&2; exit 1 ;;
esac
URL="${URL%/}"

KEYRING=/usr/share/keyrings/portege-archive.gpg
LIST=/etc/apt/sources.list.d/portege-onidia.list

fetch() {
	if command -v curl >/dev/null 2>&1; then curl -fsSL "$@"; else wget -qO- "$@"; fi
}

if fetch "$URL/portege-archive.key" 2>/dev/null | gpg --dearmor > "$KEYRING" 2>/dev/null; then
	echo "deb [signed-by=$KEYRING] $URL stable main" > "$LIST"
	echo "repository key installed: $KEYRING"
else
	echo "WARNING: no signing key at $URL/portege-archive.key" >&2
	echo "         falling back to [trusted=yes] (unsigned repository)" >&2
	echo "deb [trusted=yes] $URL stable main" > "$LIST"
fi

apt-get update
echo
echo "Done. Now:  sudo apt-get install onidia"
ADDREPO
chmod 0755 "$OUT/add-repo.sh"

echo "mkrepo: repository ready in $OUT"
echo "         rsync -a --delete $OUT/ user@server:/var/www/apt/"
echo "         then, on a client:  sudo $OUT/add-repo.sh http://server/apt"
