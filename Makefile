# Top-level build for the Onidia desktop pet and the onidia-chat window.
#
# Both projects are plain Go and ship no data files: sprites, palettes, fonts
# and the built-in characters are linked into the binaries, so packaging is
# "compile, then copy a handful of files". This Makefile drives that so one
# command produces everything a download page or an apt repository needs.
#
# DISTRIBUTING.md explains what the packages contain and how a user installs
# them; RELEASE.md is the runbook for cutting a release, signing it and
# publishing dist/apt on your own server for `apt install onidia`.

VERSION    ?= 1.0.0
MAINTAINER ?= Boyke <boi@babeh.com>
PREFIX     ?= $(HOME)/.local

# install.sh puts launchers and icons under $XDG_DATA_HOME, NOT under PREFIX, so
# `PREFIX=/opt/x make install` would still write into your home directory and the
# prefix would not be a real boundary. Pinning it to $(PREFIX)/share keeps the
# install contained; for the default PREFIX=$HOME/.local that resolves to exactly
# the path install.sh already used, so a normal install is unchanged.
INSTALL_XDG := $(PREFIX)/share
DIST       := dist
APPS       := onidia onidia-chat
ARCH       := $(shell uname -m)

# The shipped config is GENERATED, never copied out of the working tree:
# onidia-chat/chat-app.ini holds a real API key and is git-ignored, so it must
# never reach a package. This is the file that goes in instead.
CONFIG     := onidia-chat/$(DIST)/chat-app.ini

# The agents that ship with the package. onidia-chat discovers them from
# /opt/onidia/share/agents (the .deb) or ~/.config/chat-app/agents (the
# tarball installer), so a fresh install can make the pet act, play media and
# so on without anyone installing anything first.
#
# _template is excluded on purpose: its manifest parses cleanly as an agent
# called "my_agent" whose descriptions read "RENAME ME", so shipping it would
# offer the model a stub that does nothing. It is a starting point for authors,
# not a working agent.
# The trailing "*/" is what keeps README.md out, but notdir() on a path that
# still ends in a slash returns "", so the slash is stripped first.
BUNDLED_AGENTS := $(filter-out _template,$(notdir $(patsubst %/,%,$(wildcard onidia-chat/agents/*/))))

# Which build to produce. Leave unset for native (this Pi 5 = arm64), or:
#   TARGET=armhf   32-bit ARMv7: Pi 2/3, Pi Zero 2W, NanoPi NEO Core v1.1
#                  (Allwinner H3 Cortex-A7) and other armv7 boards
#   TARGET=armel   32-bit ARMv6: the ORIGINAL Pi Zero / Pi 1 (BCM2835)
#   TARGET=arm64   64-bit ARM, cross-buildable from any host
#   TARGET=amd64   x86-64 desktops
#
# armel vs armhf is not a naming preference and getting it wrong ships a binary
# that will not run: Debian's armhf requires ARMv7 + VFPv3, which the BCM2835 in
# an original Pi Zero / Pi 1 does not have, so a GOARM=7 build there dies with
# SIGILL. When in doubt, uname -m on the TARGET decides: armv6l -> armel,
# armv7l -> armhf, aarch64 -> arm64.
#
# CGO_ENABLED=0 is the default and is exported below: neither project uses cgo,
# and a static binary drops the glibc floor so one artifact runs on any Linux
# of that arch. Override CGO_ENABLED=1 if you ever add a cgo dependency.
export CGO_ENABLED ?= 0

TARGET ?=
ifeq ($(TARGET),armhf)
ARCH := armv7l
DEBARCH := armhf
GOARCH_EXPORT := GOARCH=arm GOARM=7
else ifeq ($(TARGET),armel)
ARCH := armv6l
DEBARCH := armel
GOARCH_EXPORT := GOARCH=arm GOARM=6
else ifeq ($(TARGET),arm64)
ARCH := aarch64
DEBARCH := arm64
GOARCH_EXPORT := GOARCH=arm64
else ifeq ($(TARGET),amd64)
ARCH := x86_64
DEBARCH := amd64
GOARCH_EXPORT := GOARCH=amd64
else
ARCH := $(shell uname -m)
DEBARCH := $(if $(filter aarch64 arm64,$(ARCH)),arm64,\
             $(if $(filter x86_64 amd64,$(ARCH)),amd64,\
             $(if $(filter armv7l armhf,$(ARCH)),armhf,$(ARCH))))
GOARCH_EXPORT :=
endif
export GOARM ?= 7

# Credential shapes to refuse to ship, in two tiers.
#
# SECRET_RE_BIN is applied to EVERY file including binaries. Its prefixes
# (AIza / AKIA / ghp_) are vendor-specific enough that a hit is a real key.
#
# SECRET_RE is the broader set, applied to text files only (-I). The sk- rules
# would fire constantly on Go symbol tables, where "sk-" followed by a long
# CamelCase name is just an identifier, so they must not be run over binaries.
SECRET_RE     := AIza[0-9A-Za-z_-]{35}|sk-ant-[0-9A-Za-z_-]{20,}|sk-or-v1-[0-9A-Za-z]{20,}|sk-[0-9A-Za-z]{32,}|AKIA[0-9A-Z]{16}|ghp_[0-9A-Za-z]{30,}
SECRET_RE_BIN := AIza[0-9A-Za-z_-]{35}|AKIA[0-9A-Z]{16}|ghp_[0-9A-Za-z]{30,}

# onidia-chat bakes a free-tier Gemini key into the binary on purpose (chat.go,
# defaultAPIKey, "works out of the box on this machine"). That key has been
# decommissioned, so it is ignored here. It is named explicitly rather than
# removed from the patterns: any OTHER key still fails the build, and if this
# one is ever re-issued, deleting the line below is the only change needed.
RETIRED_KEY := AIzaSyB7YR3ypNW2A-raPItTfLir-B-vKuuzyR8

.PHONY: all help build config test vet check package dist deb apt-repo \
        apt-sign install uninstall deps whisper audit archinfo verify clean

all: build

help:
	@echo 'Onidia + onidia-chat: one package, both apps, and their dependencies.'
	@echo
	@echo '  make build       compile both apps in place (onidia/, onidia-chat/)'
	@echo '  make config      render the shipped chat-app.ini'
	@echo '  make test        run both test suites'
	@echo '  make vet         go vet both modules'
	@echo '  make check       vet + test + build (the pre-release gate)'
	@echo '  make package     check, then tarball + .deb + checksums + audit'
	@echo
	@echo '  make dist        release tarball (option A)       -> dist/onidia-linux-*.tar.gz'
	@echo '  make deb         Debian package (option B)        -> dist/deb/onidia_*.deb'
	@echo '                   (one package holds onidia, onidia-chat and chat-app.ini)'
	@echo '  make apt-repo    assemble a self-hostable apt repo -> dist/apt/'
	@echo '  make apt-sign    (re)sign the repository with your GPG key'
	@echo
	@echo '  make install     install both apps from the tarball into ~/.local'
	@echo '  make deps        install the runtime deps on this machine'
	@echo '  make whisper     build a faster-whisper venv for local STT'
	@echo '  make uninstall   remove what install put in PREFIX'
	@echo '  make install DEPS=no        skip the apt step'
	@echo '  make install WHISPER=1      also build the local STT venv'
	@echo '  make audit       refuse to ship a real credential'
	@echo '  make archinfo    show what would be built, and for which arch'
	@echo '  make verify      check the tarball checksums'
	@echo '  make clean       remove build output and dist/'
	@echo
	@echo 'Overridable:'
	@echo '  make deb VERSION=1.2.3 MAINTAINER="Your Name <you@example.com>"'
	@echo '  make install PREFIX=/opt/onidia'
	@echo
	@echo 'Architecture:'
	@echo '  make dist deb TARGET=armhf   # 32-bit ARMv7  (Pi 2/3, Pi Zero 2W, NanoPi NEO Core v1.1)'
	@echo '  make dist deb TARGET=armel   # 32-bit ARMv6  (original Pi Zero / Pi 1, BCM2835)'
	@echo '  make dist deb TARGET=arm64   # 64-bit ARM    (Pi 4/5, this machine)'
	@echo '  make dist deb TARGET=amd64   # x86-64 desktops'
	@echo '  (unset = native). Static (CGO_ENABLED=0) is the default.'
	@echo '  Unsure? uname -m on the TARGET: armv6l->armel  armv7l->armhf  aarch64->arm64'
	@echo '  armel is NOT armhf relabelled: armhf needs ARMv7+VFPv3, which a Pi Zero/1 lacks.'

build:
	@# GOARCH_EXPORT is empty for a native build, so plain `make build` on this
	@# Pi 5 is unchanged; TARGET=armhf switches both modules to GOARCH=arm at once.
	$(MAKE) -C onidia build $(GOARCH_EXPORT)
	$(MAKE) -C onidia-chat build $(GOARCH_EXPORT)
	@# agentctl and preflight are developer tools and are not distributed, but
	@# they still build here so `make build` leaves a complete local tree.
	$(MAKE) -C onidia-chat agentctl $(GOARCH_EXPORT)
	$(MAKE) -C onidia-chat preflight $(GOARCH_EXPORT)

config:
	@$(MAKE) --no-print-directory -C onidia-chat $(DIST)/chat-app.ini
	@echo 'config: $(CONFIG)'
	@echo '        generated from chat-app.ini.template; the live onidia-chat/chat-app.ini'
	@echo '        is never an input and any api-key line is blanked in the output'

test:
	cd onidia   && go test -count=1 ./...
	$(MAKE) -C onidia-chat test

vet:
	cd onidia   && go vet ./...
	cd onidia-chat && go vet ./...

check: vet test build

# One coherent release cut. audit runs last so it inspects the artifacts that
# were just produced rather than whatever was left over from an older run.
package: check dist deb audit
	@echo
	@echo 'release $(VERSION) for $(ARCH) is ready in $(DIST)/'

# One package containing BOTH apps. A user installs one thing and gets the
# pet, the chat window and the config; they were only ever split because they
# are two Go modules, which is a build detail, not a packaging one.
#
# Both binaries land in /opt/onidia so one uninstall is clean, and both get a
# /usr/bin symlink. The package is named `onidia`.
#
# Runtime dependencies go in Recommends, not Depends, because the binaries are
# static (no libc even) and the apps degrade rather than refuse to start: no
# audio player means no speech, not a failed install.
#
# This list is what the two binaries actually exec, not what sounds plausible.
# onidia-chat probes ttsPlayerCandidates = aplay, paplay, ffplay and
# sttRecorderCandidates = pw-record, parecord, arecord, ffmpeg, so:
#   alsa-utils       -> aplay, arecord
#   pulseaudio-utils -> paplay, parecord
#   pipewire-bin     -> pw-record (Raspberry Pi OS)
#   pipewire-utils   -> pw-record (upstream Debian)
# The "|" alternatives are deliberate, and apt accepts them unparenthesised
# (checked against apt_pkg.parse_depends - adding parentheses breaks it).
#
# mpv/vlc used to be recommended here and were simply wrong: neither binary is
# ever invoked, so they installed dead weight and still left her with no
# working player.
DEPS_RECOMMENDS := pipewire-bin | pipewire-utils, alsa-utils, pulseaudio-utils, python3, python3-venv, xdg-utils

# Stages the bundled agents into $(DIST)/agents, minus _template. A staged
# copy rather than a --dir straight off onidia-chat/agents, because the packaging
# helpers copy a directory wholesale and the template has to stay behind.
.PHONY: stage-agents
stage-agents:
	@rm -rf $(DIST)/agents
	@mkdir -p $(DIST)/agents
	@for a in $(BUNDLED_AGENTS); do \
		cp -a onidia-chat/agents/$$a $(DIST)/agents/ || exit 1; \
	done
	@echo "  staged $(words $(BUNDLED_AGENTS)) agent(s): $(BUNDLED_AGENTS)"

dist:
	$(MAKE) -C onidia build $(GOARCH_EXPORT)
	$(MAKE) -C onidia-chat build $(GOARCH_EXPORT)
	$(MAKE) --no-print-directory -C onidia-chat $(DIST)/chat-app.ini
	$(MAKE) --no-print-directory stage-agents
	@mkdir -p $(DIST)
	packaging/mktar.sh --name onidia --version $(VERSION) --arch $(ARCH) --out $(DIST) \
		--file onidia/onidia:bin/onidia:0755 \
		--file onidia-chat/onidia-chat:bin/onidia-chat:0755 \
		--file onidia-chat/dist/chat-app.ini:share/chat-app.ini:0644 \
		--file onidia/README.md:share/README.md:0644 \
		--file DISTRIBUTING.md:DISTRIBUTING.md:0644 \
		--file RELEASE.md:RELEASE.md:0644 \
		--file packaging/install.sh:install.sh:0755 \
		--file packaging/desktop/onidia.desktop:share/onidia.desktop:0644 \
		--file packaging/desktop/onidia-chat.desktop:share/onidia-chat.desktop:0644 \
		--dir packaging/icons:share/icons \
		--dir $(DIST)/agents:share/agents
	cd $(DIST) && sha256sum onidia-linux-$(ARCH)-$(VERSION).tar.gz > SHA256SUMS
	@echo; ls -la $(DIST)

deb:
	$(MAKE) -C onidia build $(GOARCH_EXPORT)
	$(MAKE) -C onidia-chat build $(GOARCH_EXPORT)
	$(MAKE) --no-print-directory -C onidia-chat $(DIST)/chat-app.ini
	$(MAKE) --no-print-directory stage-agents
	@mkdir -p $(DIST)/deb
	@sed 's|Exec=__BIN__/|Exec=|' packaging/desktop/onidia.desktop  > $(DIST)/onidia.desktop
	@sed 's|Exec=__BIN__/|Exec=|' packaging/desktop/onidia-chat.desktop > $(DIST)/onidia-chat.desktop
	packaging/mkdeb.sh --name onidia --version $(VERSION) --arch $(ARCH) \
		--maintainer "$(MAINTAINER)" \
		--section games \
		--homepage https://github.com/portege/onidia \
		--description "Anime desktop pet (onidia) and AI chat window with its voice (onidia-chat), pure Go and raw X11" \
		--recommends "$(DEPS_RECOMMENDS)" \
		--out $(DIST)/deb \
		--file onidia/onidia:opt/onidia/bin/onidia:0755 \
		--file onidia-chat/onidia-chat:opt/onidia/bin/onidia-chat:0755 \
		--file onidia-chat/dist/chat-app.ini:opt/onidia/share/chat-app.ini:0644 \
		--file onidia/README.md:usr/share/doc/onidia/README.md:0644 \
		--file DISTRIBUTING.md:usr/share/doc/onidia/DISTRIBUTING.md:0644 \
		--file RELEASE.md:usr/share/doc/onidia/RELEASE.md:0644 \
		--file $(DIST)/onidia.desktop:usr/share/applications/onidia.desktop:0644 \
		--file $(DIST)/onidia-chat.desktop:usr/share/applications/onidia-chat.desktop:0644 \
		--file packaging/icons/onidia.svg:usr/share/icons/hicolor/scalable/apps/onidia.svg:0644 \
		--file packaging/icons/onidia-chat.svg:usr/share/icons/hicolor/scalable/apps/onidia-chat.svg:0644 \
		--dir $(DIST)/agents:opt/onidia/share/agents \
		--link /opt/onidia/bin/onidia:usr/bin/onidia \
		--link /opt/onidia/bin/onidia-chat:usr/bin/onidia-chat
	@echo; ls -la $(DIST)/deb

# Builds both .debs, then pools them into dist/apt/ (a plain static file tree
# any web server can hand out) and signs it when a secret key is available.
apt-repo: deb
	packaging/mkrepo.sh $(DIST)/deb $(DIST)/apt
	@echo
	@echo "Publish $(DIST)/apt/ over HTTP, then on a client:"
	@echo "    sudo $$(pwd)/$(DIST)/apt/add-repo.sh http://your-server/apt"
	@echo "    sudo apt install onidia   (one package: onidia + onidia-chat)"

apt-sign:
	packaging/mkrepo.sh $(DIST)/deb $(DIST)/apt --sign

# Install straight out of the tarballs, so what gets tested is what gets
# shipped: install.sh is the same script a download-page user runs. It never
# overwrites an existing ~/.config/chat-app/chat-app.ini, so a working config
# that has a real key in it is safe.
# Install straight out of the tarball, so what gets tested is what gets
# shipped: this is the same install.sh a download-page user runs, including
# the dependency step. Pass DEPS=no to skip it, or WHISPER=1 for the local
# speech-to-text venv.
#
# It never overwrites an existing ~/.config/chat-app/chat-app.ini.
DEPS    ?= yes
WHISPER ?= 0
install: dist
	@set -e; \
	  t=$(DIST)/onidia-linux-$(ARCH)-$(VERSION).tar.gz; \
	  [ -f "$$t" ] || { echo "install: missing $$t (run 'make dist' first)" >&2; exit 1; }; \
	  d=$(DIST)/.install; rm -rf "$$d"; mkdir -p "$$d"; \
	  tar xzf "$$t" -C "$$d"; \
	  args=""; \
	  [ "$(DEPS)" = "no" ] && args="$$args --no-deps"; \
	  [ "$(WHISPER)" = "1" ] && args="$$args --with-whisper"; \
	  PREFIX=$(PREFIX) XDG_DATA_HOME=$(INSTALL_XDG) \
	    "$$d"/onidia-linux-$(ARCH)-$(VERSION)/install.sh $$args; \
	  rm -rf "$$d"

# The runtime dependencies on THIS machine. The .deb gets them as
# Recommends; this is the same list for a tarball install or a dev setup.
deps:
	@echo "installing: $(DEPS_RECOMMENDS)"
	@sudo apt-get install -y $(DEPS_RECOMMENDS)
	@echo; echo "whisper is a pip package, not an apt one - for local STT:"
	@echo "    make whisper"

# faster-whisper cannot be an apt dependency: it is a pip package that pulls
# ctranslate2 wheels. It goes in its own venv, which onidia-chat is told about
# with stt-whisper-cmd. The model itself is a few hundred MB, downloaded by
# faster-whisper on first use, so nothing is fetched here.
WHISPER_VENV ?= $(HOME)/.local/share/onidia/venv
whisper:
	python3 -m venv $(WHISPER_VENV)
	$(WHISPER_VENV)/bin/pip install --upgrade pip
	$(WHISPER_VENV)/bin/pip install faster-whisper
	@echo
	@echo "ready. In ~/.config/chat-app/chat-app.ini set:"
	@echo "    stt = whisper"
	@echo "    stt-whisper-cmd = $(WHISPER_VENV)/bin/python3"

# Deliberately conservative: removes what install put there and nothing else.
# Your config and your installed agents are left alone on purpose.
#
# The chat-app names are swept up too, for someone who upgrades the checkout and
# runs this without ever re-running install.sh - those are the files a pre-rename
# install.sh left behind. Still only our own three kinds of file, never anything
# under ~/.config.
uninstall:
	@set -e; \
	for b in onidia onidia-chat chat-app; do rm -fv "$(PREFIX)/bin/$$b"; done; \
	for d in onidia onidia-chat chat-app; do \
	  rm -fv "$(INSTALL_XDG)/applications/$$d.desktop"; \
	  rm -fv "$(INSTALL_XDG)/icons/hicolor/scalable/apps/$$d.svg"; \
	done; \
	echo 'left alone: ~/.config/chat-app (your chat-app.ini, agents, whisper helper)'

# Refuse to hand out a package that contains a credential or a real config.
#
# The artifacts have to be UNPACKED first: a tarball and a .deb are both gzip
# streams, so grepping the compressed bytes for a plaintext key finds nothing
# and silently passes. That check is worse than none, because it looks like it
# ran. So each artifact is expanded into a temp dir and the real files in it
# are scanned.
#
# Two tiers, because one tier is not enough. Vendor-prefixed patterns
# (AIza / AKIA / ghp_) are run over EVERY file including binaries, since a hit
# there is a real key. The broader sk- rules run over text files only, because
# a Go binary is megabytes of symbol tables in which "sk-" followed by a long
# CamelCase identifier is ordinary and would drown the run in false positives.
# On the config: the shipped file IS named chat-app.ini, so the filename is not
# the problem. What must not ship is a POPULATED key, so the check is whether an
# api-key line has a value, not whether the file is called .example. Implemented
# as find -exec rather than a nested `while`, which cannot survive being written
# across backslash-continued make recipe lines.
audit:
	@fail=0; leak=0; \
	if [ ! -d $(DIST) ]; then echo "audit: no $(DIST)/ yet - run 'make dist deb' first" >&2; exit 1; fi; \
	tmp=$$(mktemp -d); trap 'rm -rf "$$tmp"' EXIT; \
	n=0; \
	for f in $(DIST)/*.tar.gz $(DIST)/deb/*.deb $(DIST)/apt/pool/main/*/*/*.deb; do \
	  [ -f "$$f" ] || continue; \
	  n=$$((n+1)); x=$$tmp/$$n; mkdir -p "$$x"; \
	  case "$$f" in \
	    *.tar.gz) tar xzf "$$f" -C "$$x" ;; \
	    *.deb)    dpkg-deb -x "$$f" "$$x" ;; \
	  esac; \
	  found=$$(grep -rhoaE '$(SECRET_RE_BIN)' "$$x" 2>/dev/null \
	          | grep -vxF '$(RETIRED_KEY)'; \
	         grep -rhoIE '$(SECRET_RE)' "$$x" 2>/dev/null \
	          | grep -vxF '$(RETIRED_KEY)' \
	         | sort -u); \
	  if [ -n "$$found" ]; then \
	    echo "audit: FAIL  credential-shaped string inside $$f:" >&2; \
	    echo "$$found" | sed "s|^|    |" >&2; \
	    leak=1; \
	  fi; \
	  bad=$$(find "$$x" -type f -name chat-app.ini -exec sh -c \
	        'grep -lE "^[[:space:]]*api-key[[:space:]]*=[[:space:]]*[^[:space:]]" "$$1" >/dev/null 2>&1 && echo "$$1"' \
	        sh {} \; 2>/dev/null); \
	  if [ -n "$$bad" ]; then \
	    echo "audit: FAIL  $$f ships a chat-app.ini with a populated api-key:" >&2; \
	    echo "$$bad" | sed "s|^|    |" >&2; \
	    fail=1; \
	  fi; \
	done; \
	if [ $$leak -ne 0 ]; then \
	  echo "audit: FAILED - rotate the key, then rebuild." >&2; \
	fi; \
	if [ $$fail -ne 0 ] || [ $$leak -ne 0 ]; then exit 1; fi; \
	echo "audit: ok - $$n artifact(s), no credentials, config ships blanked"

# What would be built, and for which architecture. Useful before a cross-build:
# it prints the artifact name and the GOARCH without compiling anything.
archinfo:
	@echo "host        : $$(uname -m)"
	@echo "TARGET      : $(if $(TARGET),$(TARGET),- (native))"
	@echo "go build as : $(if $(GOARCH_EXPORT),$(GOARCH_EXPORT),native $(GOARCH))"
	@echo "CGO_ENABLED : $(CGO_ENABLED)   (0 = static, no glibc floor)"
	@echo "artifacts   : $(DIST)/*.tar.gz  $(DIST)/deb/*.deb"

verify:
	cd $(DIST) && sha256sum -c SHA256SUMS

clean:
	$(MAKE) -C onidia clean
	$(MAKE) -C onidia-chat clean
	rm -rf $(DIST)
