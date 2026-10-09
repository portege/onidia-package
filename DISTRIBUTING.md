# Distributing Onidia & onidia-chat

Two apps, ONE package. `onidia` (the pet) and `onidia-chat` (the chat window)
are separate Go modules, but they ship as a single tarball and a single
`.deb` - a user installs one thing and gets both, plus the config.
Everything below is driven from the **top-level `Makefile`** in this directory:

```sh
make build        # compile the binaries where they are
make dist         # option A: versioned tarballs      -> dist/*.tar.gz
make deb          # option B: Debian package          -> dist/deb/onidia_*.deb
make apt-repo     # pool the .debs into dist/apt/ and sign them
make apt-sign     # (re)sign dist/apt with your GPG key
make verify       # check the tarball checksums
```

Every target takes overrides: `make deb VERSION=1.2.3 MAINTAINER="Your Name <you@example.com>"`.

This file is the *reference*: what is in the packages, what a user needs, and
how they install it. The *procedure* for cutting a release - signing keys,
publishing the repository, upgrades, key rotation, troubleshooting - is in
[`RELEASE.md`](RELEASE.md).

---

## What actually ships

| Binary | Installs to |
|---|---|
| `onidia` (the pet, 3.8 MB) | `/opt/onidia/bin/onidia` |
| `onidia-chat` (the chat window, 11 MB) | `/opt/onidia/bin/onidia-chat` |
| `chat-app.ini` | `/opt/onidia/share/chat-app.ini` |
| the bundled agents | `/opt/onidia/share/agents/<id>/` |

Both binaries get a `/usr/bin` symlink. `petctl`, `petui`, `preflight` and
`agentctl` are built locally but **not distributed**: `petctl`/`petui` are
developer tools, and `preflight` only duplicates a check onidia-chat already runs
internally at startup. Nothing at runtime shells out to any of them - the pet
and the chat window talk over the say/cmd FIFOs.

`agentctl` is the one omission that used to cost users something: it is the
only way to list, install or remove an agent, and it is 6 MB - larger than the
pet itself. onidia-chat grew the three equivalent modes instead, so the agent
story is reachable from the one binary that is already installed:

```
onidia-chat -agents-list                          # what is installed, and what is switched off
onidia-chat -agents-install <folder|zip|url|id>   # add one to ~/.config/chat-app/agents
onidia-chat -agents-remove <id>                   # remove one of your own
onidia-chat -agents-disabled play_movie,play_song # switch agents off without deleting them
```

They call the same `agent` package functions `agentctl` calls, and they honour
`agents-dir`, `agents-disabled`, `agents-key` and `agents-registry` from
`chat-app.ini`. `-agents-remove` refuses to touch `/opt/onidia/share/agents`:
those files belong to the package, and `dpkg` should be the only thing that
removes them.

Characters are **compiled in**, so the two executables are the whole app.

Two facts make distribution unusually easy here:

1. **No data files.** Sprites, palettes, fonts and the built-in characters are
   Go packages linked into the binary (`characters/`), so the single
   `onidia` executable *is* the whole app. The chat window only needs an INI
   file, which the user creates.
2. **Only libc.** `ldd onidia` prints `libc.so.6` and nothing else - no X11
   client library, no cgo, no GUI toolkit. There is nothing to bundle and
   nothing to install alongside the binary.

### Requirements on the user's machine

- **X11 display** (Xorg or XWayland; most Wayland compositors provide one) and
  ideally a compositor for the per-pixel transparency.
- Optional, and each one degrades instead of failing: `aplay`/`paplay`/`ffplay`
  (speech), `pw-record`/`parecord`/`arecord`/`ffmpeg` (microphone),
  `faster-whisper` in a Python venv (local STT), `mpv`/`vlc`/`cvlc`
  (media agents), and an LLM API key (`GEMINI_API_KEY`, AWS profile, or an
  OpenRouter key) for anything beyond stub mode.

### Architecture: read this before promising a download to a friend

A Go build is **per-architecture**. The packages here are built on this
machine, so they are `arm64` (the `.deb` filename says `arm64`, the tarball
says `aarch64` - same thing, uname name vs Debian name).

- Works as-is: Raspberry Pi 4/5, and any other 64-bit ARM Linux.
- **Will not** run on x86_64 (most desktops/laptops). Build there instead -
  Go cross-compiles, but the simplest correct route is to run `make dist` /
  `make deb` on a machine of that architecture, or in CI per `GOARCH`.
- 32-bit ARM needs its own build, and which one depends on the board's core:
  `make deb TARGET=armhf` (ARMv7 - Pi 2/3, Pi Zero 2W, NanoPi NEO Core v1.1)
  or `make deb TARGET=armel` (ARMv6 - the *original* Pi Zero / Pi 1, BCM2835).
  `make deb` picks the matching Debian architecture name for you. Do not use
  `armhf` for a Pi Zero/1 just because it says "Pi Zero": Debian's armhf needs
  ARMv7 + VFPv3 and that package will not execute on an ARMv6 CPU.

The apt repository handles several architectures at once: drop the `.deb`s for
each into `dist/deb/` and re-run `make apt-repo`; it emits a `binary-<arch>`
index per architecture and lists them all in `Release`.

### glibc

The binaries are dynamically linked against the C library, so they need a
glibc at least as new as the one they were built on (Raspberry Pi OS bookworm
= glibc 2.36). On older distros (Debian bullseye, Ubuntu 20.04) either build
there, or ship a fully static binary instead - the code has no cgo
dependencies, so this works and removes the constraint entirely:

```sh
CGO_ENABLED=0 make dist      # static, runs on any Linux of the same arch
```

---

## Option A - release tarball

```sh
make dist
```

Produces `dist/onidia-linux-<arch>-<version>.tar.gz` and
`dist/onidia-chat-...`, plus a `dist/SHA256SUMS` for the download page. Each
tarball contains:

```
bin/            the executables (0755)
share/README.md the app's README
share/*.desktop launcher; Exec is rewritten at install time
share/icons/    app icons for the user's icon theme
share/agents/   the bundled agents; install.sh copies them to
                ~/.config/chat-app/agents, never overwriting your own
share/chat-app.ini  the config template, copied to the XDG config dir
install.sh      self-contained installer
```

### For the user

```sh
tar xzf onidia-linux-aarch64-1.0.0.tar.gz
cd onidia-linux-aarch64-1.0.0
./install.sh
```

`install.sh` copies `bin/*` into `~/.local/bin`, installs the launcher and
icon into the XDG directories, and (onidia-chat) registers the bundled agents
and seeds `~/.config/chat-app/chat-app.ini` from the shipped config if you
have none - that is the path both programs actually read. Options:

```sh
PREFIX=/opt/onidia ./install.sh   # somewhere else
FORCE=1 ./install.sh              # overwrite what's already there
```

Nothing is written outside `~/.local` and `~/.config/chat-app`; to undo, delete
those files. Manual install works too - it is just files:

```sh
install -Dm755 bin/onidia ~/.local/bin/onidia
mkdir -p ~/.local/share/applications
sed "s|__BIN__|$HOME/.local/bin|" share/onidia.desktop \
  > ~/.local/share/applications/onidia.desktop
```

`__BIN__` is a placeholder for exactly this reason: the tarball does not know
where it will end up, so `install.sh` fills it in.

---

## Option B - Debian package

```sh
make deb        # -> dist/deb/onidia_1.0.0_arm64.deb  (contains both apps)
```

Installed layout (nothing else is touched, no maintainer scripts):

```
/opt/<name>/bin/...                       the executables
/usr/bin/onidia -> /opt/onidia/bin/onidia  (and petctl, petui, desktop-pet)
/usr/share/applications/onidia.desktop    Exec=onidia, resolved via PATH
/usr/share/icons/hicolor/scalable/apps/   app icon
/usr/share/doc/onidia/README.md
```

The binaries live in `/opt` with `/usr/bin` symlinks pointing at them, so
uninstalling is clean (`dpkg -r onidia` leaves nothing in `/usr/bin`) and two
versions can never overwrite each other. `onidia-chat` also ships
`/opt/onidia/share/chat-app.ini`.

### For the user, no repository involved

```sh
sudo apt install ./onidia_1.0.0_arm64.deb          # offline, from a file
```

After install:

```sh
onidia                                    # the pet
onidia-chat                                  # the chat window
mkdir -p ~/.config/chat-app
cp /opt/onidia/share/chat-app.ini ~/.config/chat-app/   # then add a key
onidia-chat -preflight warn                    # report what is missing, do not block
```

---

## Hosting your own apt repository

`make apt-repo` pools the `.deb`s into a standard Debian layout (`pool/` +
`dists/stable/`) and signs it when a GPG key is available, and
`dist/apt/add-repo.sh` is the one-liner a user runs to point their machine at
it:

```sh
make apt-repo
rsync -a --delete dist/apt/ user@server:/var/www/apt/
# on a client:
curl -fsSL http://your-server/apt/add-repo.sh | sudo sh -s -- http://your-server/apt
sudo apt install onidia
```

The full procedure - signing keys, the server config, upgrades, key rotation,
adding more architectures and a troubleshooting table - lives in
[`RELEASE.md`](RELEASE.md). Read that one before you publish; this file stays
the reference for what the packages contain and how a user installs them.


## Housekeeping

- `dist/` is build output and is git-ignored in both projects; nothing you
  generate here should be committed.
- The `.deb`s carry `Maintainer:` from the Makefiles (`Boyke
  <boi@babeh.com>` by default). Keep it a real address - it is what `apt`
  shows on `apt show`.
- There is no `LICENSE` file in either repository yet. Add one before
  handing these binaries to anyone outside your own server, and drop a
  `usr/share/doc/<name>/copyright` next to the README if you want the package
  to be lintian-clean; the build does not invent license terms for you.
- `packaging/` holds the shared scripts: `mktar.sh`, `mkdeb.sh`, `mkrepo.sh`
  and the `install.sh` that ships inside tarballs. Both projects' Makefiles
  call them, so the two packages never drift apart.
