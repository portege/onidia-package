# Releasing & deploying

The operational half of shipping these apps: what a release consists of, how
it is signed, how the `.deb`s reach your server, and what happens on the
user's machine afterwards. For *what* is inside the packages and the
architecture caveats, see [`DISTRIBUTING.md`](DISTRIBUTING.md) - this file
assumes you have read that.

Everything is driven from the **top-level `Makefile`**:

```sh
make dist        # release tarballs        -> dist/*.tar.gz + dist/SHA256SUMS
make deb         # Debian package          -> dist/deb/onidia_*.deb
make apt-repo    # pool + sign the repo    -> dist/apt/
make apt-sign    # rebuild the repo and force signing
make verify      # check the tarball checksums
```

All targets accept `VERSION=` and `MAINTAINER=`, e.g. `make deb VERSION=1.1.0`.

---

## What a release produces

```
dist/
├── onidia-linux-aarch64-<ver>.tar.gz     ONE tarball: onidia + onidia-chat + config
├── SHA256SUMS                            checksums of the tarball
├── deb/
│   └── onidia_<ver>_arm64.deb            ONE package, both apps
└── apt/                                  the repository you publish
    ├── pool/main/o/onidia/onidia_<ver>_arm64.deb
    ├── dists/stable/Release
    ├── dists/stable/{InRelease,Release.gpg}      (only when signed)
    ├── dists/stable/main/binary-arm64/{Packages,Packages.gz,Release}
    ├── portege-archive.key                      (only when signed)
    └── add-repo.sh                              the client-side installer
```

`dist/` is build output - it is git-ignored in both projects. `make clean`
removes it.

## The release checklist

```sh
# 0. sanity: the code still builds and the tests still pass
(cd onidia   && go test ./... && go build ./...)
(cd onidia-chat && make test)          # or: go test ./...

# 1. bump the version - one value drives every artifact name
make dist deb apt-repo VERSION=1.1.0

# 2. check what you are about to hand out
make verify
dpkg-deb --info dist/deb/onidia_*.deb

# 3. publish
rsync -a --delete dist/apt/ user@server:/var/www/apt/
```

Steps 0 and 2 are the ones people skip. `make verify` catches a truncated
download, and `dpkg-deb --info` is where a wrong `Maintainer:` or `Version:`
shows up before your users do.

## Versioning rules

- One `VERSION` for the combined package, so a release is a single, coherent cut.
- **Never reuse a version number.** apt compares versions: republishing
  `1.1.0` with different bits is invisible to clients that already have
  `1.1.0`. If you must ship a fix over an existing version, bump to `1.1.1`.
- The pool keeps every `.deb` you publish, so a client that has not upgraded
  yet can still fetch the version it was told about. Do not prune the pool
  aggressively.
- `apt` will not *downgrade*. To roll back, publish a higher version built
  from the older commit, or have users `apt-mark hold <pkg>`.
- Where the metadata lives: the **top-level `Makefile`** owns `VERSION`,
  `MAINTAINER`, `DEPS_RECOMMENDS` (the deb `Recommends:` line), `--section`,
  `--homepage` and `--description`. The per-project Makefiles under `onidia/`
  and `onidia-chat/` only build binaries and the config now; the combined package
  is assembled at the top level.

## Signing the repository

Signing is what turns `[trusted=yes]` into a real `signed-by=` check, and it
is worth doing before anyone outside your own machines installs from you.

```sh
gpg --full-generate-key          # once; a passphrase is fine, gpg-agent caches it
make apt-repo                    # signs automatically with your default key
make apt-repo GPG_KEY=<keyid>    # ...or pick the key explicitly
make apt-sign                    # rebuild the repo and force signing
```

Behaviour, so there are no surprises:

- `make apt-repo` signs when a secret key is available and leaves the repo
  **unsigned** (with a warning) when there is none. `add-repo.sh` then falls
  back to `[trusted=yes]` and says so.
- `make apt-sign` demands a key and exits non-zero without one. It rebuilds
  the tree from `dist/deb/`, so keep that directory until you are done - a
  `make clean` empties it and `apt-sign` will refuse.
- To keep release signing out of your personal key, run the build with an
  isolated `GNUPGHOME` holding only the release key:
  `GNUPGHOME=/srv/keys/release make apt-repo`.

`InRelease` and `Release.gpg` are both produced; apt prefers `InRelease` and
falls back to the detached signature, so either verification path works.

## Publishing

`dist/apt/` is a plain static file tree. Any web server will do:

```sh
rsync -a --delete dist/apt/ user@server:/var/www/apt/
```

nginx, if that is what serves the site:

```nginx
location /apt/ {
    alias /var/www/apt/;
    autoindex on;              # so people can browse and grab a .deb directly
    default_type application/octet-stream;
}
```

```sh
sudo nginx -t && sudo systemctl reload nginx
```

It can live under the same web root as the rest of your site - one hostname,
one nginx block - as long as the `/apt/` prefix maps to the directory above.
Put it behind TLS if you host it publicly: apt over plain HTTP is a
downgrade invitation.

## On the user's machine

```sh
curl -fsSL http://your-server/apt/add-repo.sh | sudo sh -s -- http://your-server/apt
sudo apt install onidia
```

`add-repo.sh` does exactly three things, so removing the repository is
symmetric - delete the keyring and the one list file, then `apt-get update`:

| file | written by | holds |
|---|---|---|
| `/usr/share/keyrings/portege-archive.gpg` | `add-repo.sh` | the repository public key |
| `/etc/apt/sources.list.d/portege-onidia.list` | `add-repo.sh` | one `deb [signed-by=...] ... stable main` line |

By hand, the same three commands:

```sh
curl -fsSL http://your-server/apt/portege-archive.key | gpg --dearmor \
  | sudo tee /usr/share/keyrings/portege-archive.gpg >/dev/null
echo 'deb [signed-by=/usr/share/keyrings/portege-archive.gpg] http://your-server/apt stable main' \
  | sudo tee /etc/apt/sources.list.d/portege-onidia.list
sudo apt update
```

`stable` is the suite/codename and must keep matching the `dists/stable/`
directory; it is the only part of the URL a user ever types.

## Upgrading

```sh
make dist deb apt-repo VERSION=1.2.0
rsync -a --delete dist/apt/ user@server:/var/www/apt/
```

```sh
# on a client
sudo apt update && sudo apt upgrade
```

`--delete` on the rsync is what removes superseded index files; without it a
stale `Packages` entry can linger and confuse clients. Tarball users get the
new `SHA256SUMS` on the download page and re-run `./install.sh FORCE=1`.

## Key rotation

```sh
gpg --quick-generate-key 'Portege Release <boi@babeh.com>' rsa4096 sign 1y
make apt-repo GPG_KEY=<new-keyid>
rsync -a --delete dist/apt/ user@server:/var/www/apt/
```

Then have users re-run `add-repo.sh` (or install the new key alongside the
old one). Keeping the retired key in users' keyrings is harmless; removing it
outright breaks anyone who has not updated yet.

## Adding another architecture

The repository is multi-arch by construction: drop each architecture's `.deb`
into `dist/deb/` and re-run `make apt-repo`. It writes a `binary-<arch>`
index per architecture, lists them all in `Release`, and names the `.deb`
files with the Debian architecture (`arm64`, `amd64`, `armhf`, `armel`).

In practice: build on the target architecture (or with `GOARCH` set for that
target) and copy the resulting `.deb` in. Remember the arm64 binaries in
`dist/deb/` are specific to this machine, so a multi-arch repository needs one
build machine per architecture.

## Troubleshooting

| symptom | cause and fix |
|---|---|
| `apt update`: `NO_PUBKEY` | the client has the list but not the key - re-run `add-repo.sh` |
| `apt update`: `Hash Sum mismatch` | the index changed mid-transfer, or a stale `Packages` was published - re-run `rsync` with `--delete` |
| `apt install`: `E: Unable to locate package` | the `dists/stable/main/binary-<arch>/` index for the client's architecture does not exist; check `Architectures:` in `dist/apt/dists/stable/Release` |
| `apt install`: `E: The following packages have unmet dependencies` | nothing here should - the `.deb`s have no `Depends`; report it, it means a bad build |
| `mkrepo: --sign given but no GPG secret key` | no key in the keyring - create one, or set `GPG_KEY` |
| signing hangs on a passphrase | `gpg-agent` is not caching it in a non-interactive session - use a key without a passphrase, or run the build in your own session |
| `apt install onidia` then `onidia: command not found` | the `/usr/bin/onidia` symlink was removed to resolve a name clash with another package; run `/opt/onidia/bin/onidia` |
| `onidia-chat` exits with `preflight: BLOCKED` | not a packaging problem: the backend has no usable credentials. Start it with `onidia-chat -preflight warn` for the full report instead of the block |
| published `.deb` runs nowhere | architecture mismatch - the packages here are arm64; see `DISTRIBUTING.md` |
