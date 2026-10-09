Good instinct — that requirement changes the architecture's spine. Since the codebase ships with a real precedent for exactly this (the pet and onidia-chat integrate across *processes* via FIFOs; `petctl` is a separate build that drives the pet; characters can be zipped into distributable packs), here's the modular-agent design built around "download → drop in → auto-detected."

## The two "library" delivery models (and what I recommend)

Your question "as a library?" has two valid answers in this codebase, and they're complementary:

| Model | Distribution unit | How it integrates | Rebuild needed? |
|---|---|---|---|
| **A. V package** (like `github.com/portege/onidia/characters`, `github.com/jezek/xgb`) | a `github.com/…` package in `go.mod`; agent code blank-imported in `main.go`, registers itself in `init()` | natural fit for the existing ecosystem; type-safe; in-process | yes (recompile) |
| **B. Standalone agent executable + manifest** (like the pet itself / `petctl`) | a folder or zip dropped into `~/.config/chat-app/agents/` containing `agent.json` + an executable (any language) | **auto-detected on startup by a directory scan, hot-integrated, no rebuild, no code change** | **no** |

**Recommendation: make B the primary path** — because it's what your sentence ("download → detect → integrated automatically") actually demands, and it makes "anyone" able to write an agent in *any* language (Python, shell script, Go/V, Rust…). Keep A as an optional native fast-path for agents that need in-process access to the pet bridge or heavy libraries. Both end up behind the **same `Agent` interface**, so the brain, parser, config and catalog don't care which model an agent came from.

## Architecture

```
                         ┌─ AgentRegistry (agents.go) ─ one unified catalog ─┐
                         │                                                  │
   discovered at startup │   NativeAgent (in-process V, model A)            │
   ──────────────────────┤   ExternalAgent adapter (spawns + drives         │
   scans agents/ dirs    │       the process over the socket, model B)      │
   for agent.json        │                                                  │
                         └───────────────┬──────────────────────────────────┘
                                         ▼ dispatch (params validated centrally)
                    agents/play_song/ agent.mp3-index "Havana" ─▶ mpv ...
```

**The external agent contract** (the heart of "anyone can make one"):

`agent.json` — the manifest that makes an agent self-describing and auto-detectable:
```json
{
  "id": "play_song",
  "version": "1.2.0",
  "description": "Play a track from the local music library.",
  "params": [
    {"name": "title",   "type": "string", "required": true,  "desc": "Song title or artist"},
    {"name": "shuffle", "type": "bool",   "required": false, "desc": "Play random tracks"}
  ],
  "exec": {"cmd": "./agent_song", "protocol": "agent-line-v1"}
}
```

A tiny line protocol (same spirit as the pet's `.say`/`.cmd` FIFOs — already proven in this repo):
```
brain → agent:  READY <id> <version>            # or brain reads the manifest and skips
brain → agent:  RUN {"title":"Havana","shuffle":false}
agent → brain:  OK Playing "Havana" by Camila Cabello   (visible in the chat bubble)
agent → brain:  ERR no track matching "Havana"          (brain lets the LLM rephrase)
agent → brain:  END
```
The brain parses the **manifest** into a `ParamSpec` (feeding the auto-generated system-prompt catalog and central validation), and drives the process with argv arrays — **the LLM's text is never interpolated into a shell**, same safety rule as before.

**Download & install UX** (extends `cmd/agentctl`):
```
agentctl search              # list what's in ~/.config/chat-app/agents/
agentctl install <zip|dir|url>   # validate manifest, checksum, unpack, hot-load
agentctl list               # show catalog incl. version + source
agentctl reload             # rescan dirs, pick up new agents without restart
```
A dropped-in zip is detected on the next startup (or `agentctl reload`) — that's the "it works automatically" path. Packaging follows the characters precedent: `make pack` → a zipped unit people drop in.

## Modularity rules baked into the design

1. **One discovery mechanism, one registry** — `agents.go` holds every agent (native + external); `Catalog()` still auto-builds the system-prompt block, so a newly downloaded agent is *immediately visible to the AI* with zero prompt edits.
2. **One interface, two adapters** — `ExternalAgent` is just an `Agent` implementation; if anyone wants a native V agent they implement the same 8 lines.
3. **Isolation & safety** — external agents run as unprivileged child processes with a timeout + kill-on-cancel token; ids must match `^[a-z][a-z0-9_]{1,31}$`; params validated centrally against the manifest; per-agent enable/disable in `[agents]` config.
4. **Versioning & collisions** — duplicate ids: built-in wins unless the external has a higher `version` (warn); a `DISABLED` manifest field lets a downloaded agent opt out per machine.
5. **Crash resilience** — a crashed/hung agent fails that one call with a graceful message and never takes down the chat (same non-blocking pattern as `petSayLine`/ENXIO today).

## What changes vs. the earlier plan

- **New files:** `agentdiscovery.go` (dir scan + manifest parsing + hot-load), `externalagent.go` (process spawn + line-protocol adapter), `agents/doc/AGENT-PROTOCOL.md` + `agents/template/` (manifest + a 30-line example agent in shell *and* Python) — the "how to write your own" kit.
- **`cmd/agentctl`** grows `install` / `reload` / `search`.
- **Zip packaging** for agents (same idea as `characters` `make pack`).
- **Phase 4 from before** (out-of-process socket protocol) moves up to **Phase 0/1**, because it's now the primary model.
- Everything else stands: `[AGENT: …]` tag protocol first → native function-calling later; the three reference agents (`play_song`, `play_movie`, `read_story`) get shipped *both* as built-in natives (fast, tested) *and* as downloadable examples.

## Build order (updated)

| Phase | Scope | Exit criteria |
|---|---|---|
| **0 — Core + discovery** | `agents.go` (interface/registry/catalog), manifest parser, `agentdiscovery.go` dir scan, `externalagent.go` socket adapter, `agentctl install/reload/list` | drop a zip in the agents dir → `agentctl list` shows it, no rebuild |
| **1 — First agents, both models** | `play_song` / `play_movie` / `read_story` as native agents **and** as independently downloadable self-contained agents; tests using a fake player + a fake external agent | "play Havana" works in chat; the downloadable example agents work on a fresh agents dir |
| **2 — Brain loop** | tool results → re-prompt, cancel token, per-agent concurrency, `-agents off` | chained/multi-agent requests work |
| **3 — Native tool-calling** | per-provider function calling, text-tag fallback kept | structured args |
| **4 — Ecosystem** | `agentctl` registry/update command, checksums, optional signed manifests, a community docs page | third party ships an agent zip that integrates untouched |

Two decisions to confirm before I start writing code: **(1)** the external-agent protocol — line-based (`agent-line-v1` above) vs full JSON-RPC (I recommend line-based; it matches the repo's FIFO simplicity and is trivial to implement in any language), and **(2)** whether downloaded agents run as-is from a user writable folder is acceptable for v1, or you want a stricter installed-location policy (e.g. only `~/.config/chat-app/agents` with checksums). Tell me those two, toggle to Act mode, and I'll build Phase 0 — registry + discovery + `agentctl install`, end-to-end with a sample downloadable agent.