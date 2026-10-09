# Stock chart image — plan for a different model to pick up

## Context (read this first)

- Repo root: `/home/boi/repo/ai-helper`.
- Chat app: `onidia-chat/` (Go, `CGO_ENABLED=0`, static binaries for
  x86_64 + ARM, bitmap font + TTS — no webview, no HTML).
- New agents live in `agents/` (untracked extras, installed by hand).
  Reference: `agents/sysusage/`. New work: `agents/stock/` and
  `agents/weather/`, `agents/ai_news/`.
- Agent wire protocol: `onidia-chat/docs/AGENT-PROTOCOL.md`,
  version `agent-line-v1`. Brain -> agent: `RUN {json}\n` on stdin.
  Agent -> brain on stdout: optional `INFO`, optional `PET`, terminal
  `OK <message>` / `ERR <text>`, then exit. One process per run.
- Go parser: `onidia-chat/agent/external.go` (`ExternalAgent.Run`).
  `agent.Result` in `onidia-chat/agent/agent.go` is `{Message, PetCmd, Image}`.
- Bridge: `onidia-chat/agentbridge.go` (`agentRun`, `runAgentCall`,
  `runToolCall`, `agentPetCmd`). Reply assembly: `onidia-chat/chat.go`
  `finishReply` -> `ReplyResult`. Model feedback: `onidia-chat/toolcalls.go`.
- Pet bubble: `onidia/bubble.go:437 RenderBubbleMedia` already draws an
  image <=260x200px above a caption — the short path for rendering.
- Pre-existing bug (do NOT fix in this plan unless stated): reader drops
  everything after the first `OK` line, so agents emit ONE `OK` line
  joined with `" | "`. `agentctl run sysusage section=all` shows it.

## Problem

`agents/stock/stock.py` returns text only, e.g.
`AAPL 333.15 USD +4.12% today | trend -1.55% over 7d: 338.40>#.~.~~~>333.24`.
The `#.~.~~~` ASCII sparkline reads as random characters. User wants a
real graph, not glyphs.

## Decision (researched, agreed)

- NOT HTML: would ship a webview into every install (first cgo/GUI dep,
  breaks the static ARM/x86 build), hands script execution to downloaded
  agent zips, and breaks the bitmap font + TTS. Rejected.
- Instead: typed-result `IMG` protocol — agent attaches one PNG,
  app renders it in its own style (pet bubble box), TTS keeps reading
  the `OK` text. Trust model unchanged (bytes aren't instructions).

## Phases

### Phase 1 — Protocol spec (DOC ONLY)
File: `onidia-chat/docs/AGENT-PROTOCOL.md`.
Add `IMG png <base64>` line to the wire diagram + a `### Attach one
chart image (IMG)` section after the PET section:
- `agent -> brain  IMG png <base64>\n  # optional, before OK: one chart`.
- At most ONE per run, only before `OK`/`ERR`; second = protocol error
  (same rule as PET).
- Shape: `IMG png <base64>` — kind must be `png`, payload is base64
  (std encoding, single line, no whitespace) of PNG bytes.
- Caps: encoded bytes <= 400KB total line must fit the 1MB scanner;
  decoded PNG <= 800x600px (examples target 260x200 for the bubble box).
- PNG magic bytes validated, decoded with `image/png`; anything else =
  run fails with `agent <id>: IMG: ...`, reply discarded (like bad PET).
- On `ERR` runs the image is dropped (same as PET).
- Native agents do `agent.Result{Image: img}` (`image.Image`).
- Text `OK` message is still required — it is the TTS/fallback caption.
Accept: doc renders, example agent snippet shows `IMG` before `OK`.

### Phase 2 — `onidia-chat/agent/` package (PARSER)
Files: `onidia-chat/agent/agent.go`, `onidia-chat/agent/external.go`,
  `onidia-chat/agent/external_test.go`.
- `agent.go`: add `Image image.Image` to `Result` + doc comment.
- `external.go`: parse `IMG` line in the scan loop (next to PET);
  base64-decode, check PNG magic, `png.Decode`, enforce byte + dim caps;
  duplicate/invalid = `imgErr`, returned as
  `fmt.Errorf("agent %s: %w", id, imgErr)` on the OK path (mirror PET).
- Tests in `external_test.go` with the existing `runScriptAgent` helper:
  valid PNG passes through; duplicate fails; bad base64 fails;
  non-PNG fails; oversized dims fail; image on ERR run is dropped.
Accept: `go test ./agent/` green.

### Phase 3 — Bridge + reply (PLUMBING)
Files: `onidia-chat/agentbridge.go`, `onidia-chat/chat.go`,
  `onidia-chat/toolcalls.go`, tests in `agentbridge_test.go`, `chat_test.go`.
- `agentRun` gains `Image image.Image`; `agentImg(runs)` helper like
  `agentPetCmd`; plumb in `runAgentCall` + `runToolCall`.
- `finishReply`: agent image wins over `[IMG:]` model tag, sets
  `ReplyResult.Image`.
- `toolcalls.go`: model feedback stays text-only + `(+chart image)` note.
Accept: bridge + chat tests green.

### Phase 4 — `agentctl run` (DEBUG HOOK)
File: `onidia-chat/cmd/agentctl/main.go`.
- On run success with image: save PNG to temp file, print path.
Accept: `agentctl run stock symbol=AAPL` prints a viewable PNG path.

### Phase 5 — `agents/stock/stock.py` (CHART)
- Stdlib-only PNG encoder (`zlib` + `struct`, no PIL): 260x200 line
  chart of adjusted closes, emit `IMG png <base64>` before `OK`.
- Keep current one-line text (it is the TTS caption).
Accept: `agentctl run stock symbol=AAPL history=7d` shows PNG + text.

### Phase 6 — Verify
`go test ./...`, `agentctl validate`, live `agentctl run` for
`stock`, `weather`, `ai_news`, `sysusage`.

## Current status
- Phase 1 DONE: wire diagram + `### Attach one chart image (IMG)` in
  `onidia-chat/docs/AGENT-PROTOCOL.md` (caps 400KB / 800x600, PNG-only,
  duplicate = protocol error, `OK` text still required as TTS caption).
- Phase 2 DONE: `Result.Image image.Image` in `agent/agent.go`;
  `IMG` scan + `decodeAgentImage` (base64, PNG magic, `png.Decode`,
  byte + dim caps) in `agent/external.go`; `TestExternalAgentImg`
  (valid, optional, ERR-drop, duplicate, 6 malformed incl. oversize) in
  `agent/external_test.go`. `go test ./agent/` green, gofmt/vet clean.
- Phase 3 DONE: `agentRun.Image` + `agentImg(runs)` first-wins helper in
  `agentbridge.go`; `runAgentCall` + `runToolCall` plumb it; `finishReply`
  gives the agent image priority over the `[IMG:]` model tag and sets
  `ReplyResult.Image`; `toolcalls.go` model feedback stays text-only plus
  a `(+chart image)` note. `TestAgentImg` + `TestFinishReplyAgentImageWins`
  and the chat test green.
- Phase 4 DONE: `cmdRun` prints `(chart image: /tmp/agentctl-chart-*.png)`
  when `res.Image != nil` — `saveChart` = `os.CreateTemp` + `png.Encode`,
  a failed save is a stderr note, not a failed run.
  `TestAgentctlRunPrintsChartImagePath` runs a script agent emitting
  `IMG png <base64>`, parses the printed path, re-decodes and checks dims.
- Phase 5 DONE: stock.py draws a 260x200 RGB PNG with a stdlib encoder
  (`zlib` + `struct`, own 3x5 digit font: frame, three guide lines with
  margin prices, first/middle/last date ticks, Bresenham close line
  green/red/blue, 3px cap on last close) and emits `IMG png <base64>`
  before OK for 7d/30d; history=none stays quote-only (no window, no
  picture); any chart failure emits `INFO chart skipped: ...` and the
  quote still ships. Encoded chart ~1.7KB (caps: 400KB / 800x600).
  Manifest description + history param now mention the attached chart.
- Phase 6 DONE: gofmt + `go vet ./...` clean, `go test ./...` all ok.
  `agentctl validate` ok for stock/weather/ai_news/sysusage. Live runs:
  stock AAPL 7d and NVDA 30d (caption + chart path + pet), AAPL
  history=none (quote only, no chart line), weather Berlin, ai_news
  chips, sysusage section=all. Go `image/png` decodes the Python
  output at 260x200 with ~1500 non-white pixels (frame+grid+line+labels).
- `agents/stock/` verified live (AAPL/NVDA); Yahoo Finance
  chart+search source, ~15min delayed (manifest says so).
