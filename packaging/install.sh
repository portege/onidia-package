#!/bin/sh
# install.sh - installs onidia + onidia-chat from one tarball into ~/.local.
#
# What it does:
#   * installs the runtime dependencies (apt) unless --no-deps
#   * copies bin/onidia and bin/onidia-chat -> $PREFIX/bin
#   * installs share/*.desktop -> the user's applications directory
#   * installs share/icons/*   -> the user's icon theme
#   * seeds ~/.config/chat-app/chat-app.ini from share/chat-app.ini, but ONLY
#     when absent (that is the path onidia-chat actually reads)
#   * optionally builds a faster-whisper venv (--with-whisper), because that
#     is a pip package, not a Debian one, and cannot go in Recommends
#
# Usage: ./install.sh                 # into ~/.local
#        PREFIX=/opt/onidia ./install.sh
#        FORCE=1 ./install.sh         # replace already-installed files
#        ./install.sh --no-deps       # skip the apt step entirely
#        ./install.sh --with-whisper  # also pip install faster-whisper (large)
set -eu

cd "$(dirname "$0")"
FORCE="${FORCE:-0}"
PREFIX="${PREFIX:-$HOME/.local}"
DEPS=1
WHISPER=0

for arg in "$@"; do
  case "$arg" in
    --no-deps)     DEPS=0 ;;
    --with-whisper) WHISPER=1 ;;
    -h|--help)     sed -n '2,18p' "$0"; exit 0 ;;
    *) printf 'install: unknown option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
die()  { say "install: $*" >&2; exit 1; }

[ -n "$PREFIX" ] || die "PREFIX must be set"
command -v install >/dev/null 2>&1 || die "install(1) not found"

BINDIR="$PREFIX/bin"
APPSDIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONDIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"

mkdir -p "$BINDIR" "$APPSDIR" "$ICONDIR"

# ---------------------------------------------------------------------------
# 1. dependencies
#
# These are the exact tools the apps probe for at runtime (see
# onidia-chat/internal/preflight/env.go). They are Recommends in the .deb for the
# same reason they are installed here best-effort: every one of them is
# optional, and a missing one turns a feature off rather than stopping install.
# ---------------------------------------------------------------------------
want() {
  # want <command> -> 0 if any of the given binaries is already on PATH
  for c in "$@"; do
    command -v "$c" >/dev/null 2>&1 && return 0
  done
  return 1
}

APT_PKGS=""
need() {
  # need <command...> --<apt packages...>
  want "$@" && return 0
  for p in "$@"; do
    case "$p" in --*) continue ;; esac
    case " $APT_PKGS " in
      *" $p "*) : ;;
      *) APT_PKGS="$APT_PKGS $p" ;;
    esac
  done
  return 0
}

need pw-record pw-play   -- pipewire-bin pipewire-utils
need aplay paplay        -- alsa-utils
need python3             -- python3 python3-venv
need mpv vlc             -- mpv vlc
need xdg-open            -- xdg-utils

if [ "$DEPS" -eq 1 ] && [ -n "$APT_PKGS" ]; then
  if command -v apt-get >/dev/null 2>&1; then
    if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi
    say "install: fetching dependencies:$APT_PKGS"
    # shellcheck disable=SC2086
    if $SUDO apt-get install -y $APT_PKGS; then
      say "install: dependencies installed"
    else
      say "install: WARNING - could not install:$APT_PKGS"
      say "         the apps still run; the features above are simply off"
    fi
  else
    say "install: no apt-get here (not Debian?) - skipping:$APT_PKGS"
  fi
else
  say "install: dependencies skipped (already present, or --no-deps)"
fi

# ---------------------------------------------------------------------------
# 2. binaries
# ---------------------------------------------------------------------------
n=0
for b in bin/*; do
  [ -f "$b" ] && [ -x "$b" ] || continue
  name="${b#bin/}"
  if [ -e "$BINDIR/$name" ] && [ "$FORCE" != 1 ]; then
    say "  ~ keep    $BINDIR/$name (already installed; FORCE=1 to replace)"
  else
    install -m 0755 "$b" "$BINDIR/$name"
    say "  ~ write   $BINDIR/$name"
  fi
  n=$((n + 1))
done
[ "$n" -gt 0 ] || die "no executables in bin/ - unpack the tarball first?"

# ---------------------------------------------------------------------------
# 3. launchers (the __BIN__ placeholder is resolved here)
# ---------------------------------------------------------------------------
for d in share/*.desktop; do
  [ -f "$d" ] || continue
  dest="$APPSDIR/${d#share/}"
  if [ -e "$dest" ] && [ "$FORCE" != 1 ]; then
    say "  ~ keep    $dest (FORCE=1 to replace)"
    continue
  fi
  sed "s|__BIN__|$BINDIR|g" "$d" > "$dest"
  chmod 0644 "$dest"
  say "  ~ launcher $dest"
done

# 4. icons
if [ -d share/icons ]; then
  for i in share/icons/*; do
    [ -f "$i" ] || continue
    install -m 0644 "$i" "$ICONDIR/${i#share/icons/}"
    say "  ~ icon     $ICONDIR/${i#share/icons/}"
  done
fi

# 4b. the name this window used to ship under.
#
# It was "chat-app"; it is "onidia-chat" now. No user DATA is touched here -
# ~/.config/chat-app, chat-app.ini, the downloaded agents and the whisper helper
# all stay exactly where they are, so an existing install keeps working as it is.
# What does get removed is the old binary, launcher and icon, because leaving
# those behind is not a harmless leftover: it is a second chat window and a
# second entry in the applications menu.
#
# Only the three paths this script itself installed are touched, and each removal
# is printed, so anything unexpected shows up in the log rather than vanishing.
#
# The .deb needs none of this: dpkg drops files that were in the old version of
# the package and are not in the new one, so /usr/bin/chat-app and
# /usr/share/applications/chat-app.desktop are cleaned up on upgrade by apt.
for old in "$BINDIR/chat-app" "$APPSDIR/chat-app.desktop" "$ICONDIR/chat-app.svg"; do
  if [ -e "$old" ]; then
    rm -fv "$old"
    say "  ~ removed  $old (this app is now called onidia-chat)"
  fi
done

CFGDIR="${XDG_CONFIG_HOME:-$HOME/.config}/chat-app"
CFG="$CFGDIR/chat-app.ini"

# 5. bundled agents.
#
# These are what make onidia-chat able to drive the buddy (pet_control), play media
# and so on. Without them a fresh install has nothing but the built-in story
# agent, and the model has no way to make onidia act. They go into the SAME
# directory the app scans first, ~/.config/chat-app/agents.
#
# Never overwrite an agent the user already has: it may be their own, or a
# newer copy than the one in the tarball.
AGENTSDIR="$CFGDIR/agents"
if [ -d share/agents ]; then
  copied=0; kept=0
  for a in share/agents/*; do
    [ -d "$a" ] || continue
    name="${a##*/}"
    if [ -e "$AGENTSDIR/$name" ] && [ "$FORCE" != 1 ]; then
      kept=$((kept + 1))
      continue
    fi
    mkdir -p "$AGENTSDIR"
    cp -a "$a" "$AGENTSDIR/"
    copied=$((copied + 1))
  done
  say "  ~ agents   $AGENTSDIR ($copied installed, $kept kept)"
fi

# 6. config.
#
# It goes in the app's XDG config dir, NOT ~/. That is not a style choice:
# onidia-chat looks for chat-app.ini in ./, then next to the binary, then
# $XDG_CONFIG_HOME/chat-app - and a package install puts the binary in
# /opt/onidia/bin and the config in /opt/onidia/share, so neither of the first
# two can see it. ~/.config/chat-app is already where this app keeps its agents
# and its whisper helper.
#
# Never overwrite: that file holds the user's key, and clobbering it on upgrade
# is the worst possible moment to lose one.
if [ -f share/chat-app.ini ]; then
  if [ -e "$CFG" ]; then
    say "  ~ keep     $CFG (already present)"
  elif [ -e "$HOME/chat-app.ini" ]; then
    # An older install put it here, and onidia-chat never read this location, so
    # moving it is what makes that config start taking effect.
    mkdir -p "$CFGDIR"
    mv "$HOME/chat-app.ini" "$CFG"
    say "  ~ moved    $HOME/chat-app.ini -> $CFG (the app reads this one)"
  else
    mkdir -p "$CFGDIR"
    install -m 0644 share/chat-app.ini "$CFG"
    say "  ~ wrote    $CFG (drop your API key here)"
  fi
fi

# 7. optional local speech-to-text. faster-whisper is a pip package with
#    ctranslate2 wheels, so it cannot be an apt Recommends; and the model
#    download is a few hundred MB, so it is opt-in rather than automatic.
if [ "$WHISPER" -eq 1 ]; then
  VENV="${WHISPER_VENV:-$HOME/.local/share/onidia/venv}"
  if command -v python3 >/dev/null 2>&1; then
    say "install: building faster-whisper venv at $VENV (this takes a while)"
    python3 -m venv "$VENV" || die "python3 -m venv failed (install python3-venv)"
    "$VENV/bin/pip" install --quiet --upgrade pip || true
    "$VENV/bin/pip" install --quiet faster-whisper || die "pip install faster-whisper failed"
    say "install: whisper ready. Enable it with:"
    say "    stt-whisper-cmd = $VENV/bin/python3   (in $CFG)"
    say "    stt = whisper"
  else
    say "install: no python3 - skipping whisper"
  fi
fi

# 7. what is actually available now, so a missing feature is visible here
#    rather than the first time someone presses the mic button.
say ""
say "Installed into $BINDIR. Checking what works:"
for c in onidia onidia-chat; do
  [ -x "$BINDIR/$c" ] && say "  ok   $c" || say "  MISS $c"
done
if want pw-play paplay aplay ffplay; then say "  ok   speech out (tts)"
else say "  --   speech out: no player found"; fi
if want pw-record parecord arecord ffmpeg; then say "  ok   speech in (stt)"
else say "  --   speech in: no recorder found"; fi
if want python3; then say "  ok   python3 (agents)"
else say "  --   python3 missing"; fi
if [ -d "$HOME/.config/chat-app/venv" ] || [ -d "$HOME/.local/share/onidia/venv" ]; then
  say "  ok   local whisper venv"
else
  say "  --   local whisper: not installed (re-run with --with-whisper)"
fi

cat <<EOM

  * run the pet:      $BINDIR/onidia
  * run the chat:     $BINDIR/onidia-chat      (key: $CFG or \$GEMINI_API_KEY)
  * see what is missing: $BINDIR/onidia-chat -preflight warn   (logs instead of blocking)
  * config:            $CFG
  * a launcher was added to your applications menu (log out/in if it doesn't show)
  * add to your PATH: export PATH="$BINDIR:\$PATH"
EOM
