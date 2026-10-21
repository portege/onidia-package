# agents — optional extras, installed by hand

This folder is **not part of any repository** and **not shipped** with the
package. `onidia` and `onidia-chat` bundle five agents; anything in here is an
extra you install yourself, so a machine only gets it if you ask for it.

```sh
onidia-chat -agents-install agents/sysusage     # or the folder / .zip / URL
onidia-chat -agents-install agents/weather       # both of these need internet
onidia-chat -agents-install agents/ai_news       # ... and no API key
onidia-chat -agents-list                        # check it arrived
```

Then **restart the chat window** — discovery runs at startup only. To remove it
again: `onidia-chat -agents-remove sysusage`.

## What's here

| Agent | What it does |
|---|---|
| [`sysusage/`](sysusage/) | CPU, memory, load average and disk I/O, read from `/proc` |
| [`weather/`](weather/) | current conditions + a short forecast for a place you name, via Open-Meteo |
| [`ai_news/`](ai_news/) | the latest headlines on AI (or any topic), via Google News, falling back to Hacker News |
| [`stock/`](stock/) | quote + 7/30-session history for any ticker, via Yahoo Finance, no key, ASCII sparkline today |
| [`dns/`](dns/) | any DNS record type for a domain (A, AAAA, MX, NS, TXT, ... by name or number), reverse lookups, `type=all` paged overview - hand-rolled wire client, no key |
| [`traceroute/`](traceroute/) | hop-by-hop IPv4 route to a host with RTTs and reverse DNS, one hop per page - kernel error queue, so no root and no external tools |

Both network agents need an internet connection, and neither needs an API key —
which is the whole reason they can live in this folder at all: a downloaded
agent never gets one of your keys.

## Writing your own

Copy [`sysusage/`](sysusage/) and edit it, or start from the shipped template.
The contract is one `RUN {json}` line in, one `OK`/`ERR` line out — see
[`../onidia-chat/docs/AGENT-PROTOCOL.md`](../onidia-chat/docs/AGENT-PROTOCOL.md).
A folder only needs an `agent.json` at its root; keep it self-contained, because
one folder is one unit of install.

## Before you format a reply nicely

The reply is drawn by a 5×9 bitmap font and spoken by TTS, which constrains
formatting more than you would expect. All three of these bite:

1. **No Unicode box drawing.** The font has no glyph for `─│┌┐└┘` or any other
   U+2500 block character. An unknown rune advances and draws *nothing*, so a
   neat markdown table renders as blank gaps. Every printable ASCII rune has a
   glyph (the backtick excepted), and the font is fixed-pitch, so `#`/`-`/`|`
   columns line up exactly.

2. **A newline does not survive the round trip — put the whole reply on one
   line.** `onidia-chat` turns every newline in a reply into a form feed and a
   two-page reply into a paged bubble, which is what the docs promise. But the
   protocol reader in `onidia-chat/agent/external.go` stops scanning at the
   first `OK`-prefixed line and discards everything after it, so an `OK` whose
   message contains newlines reaches the user with **only its first line**.
   That is silent — no error, no warning. It is not hypothetical: run
   `./onidia-chat/agentctl -dir agents run sysusage section=all` and you get
   one metric out of four. Join with `" | "` instead, and let the bubble
   word-wrap; `weather` and `ai_news` are written that way.
   **One exception: a raw form-feed byte (`\f`) inside the single OK line is
   not a newline** — the reader passes it through, and both the chat window
   (`ui.go splitPages`) and the buddy bubble (`onidia/bubble_page.go
   paginate`) render it as an explicit page break with `<` `>` controls.
   `dns` (type=all) and `traceroute` page long output that way, one record
   type or one hop per page.

3. **A bubble is ~25 characters wide** at the default 456px window — three
   quarters of the window, at 12px per glyph. Design to that, or launch with
   `-w 900` for roughly 59 characters. A long single line wraps within the
   bubble and is perfectly readable; it just costs vertical space.

Anything from the internet needs ASCII-folding before it reaches a bubble, and
that is not optional polish: news copy and place names arrive with curly
quotes, em dashes and accents, and each one is a rune the font cannot draw.
Both new agents share a `fold()` that unescapes entities, maps the typographic
characters, decomposes accents (`Zürich` → `Zurich`) and turns anything left
over into `?`. Copy it rather than writing a third version.

`sysusage` is a worked example of the formatting rules: ASCII bar meters
instead of box rules, and every line sized to fit the default window.

## Before you emit a `PET` line

The wire format accepts **`action`**, **`event`**, **`expr`**, or a movement
verb. It does **not** accept `expression` — that is `pet_control`'s *input*
vocabulary, and the two are easy to confuse because they hold the same names.

This fails loudly: an unknown verb is a protocol error that **discards the
whole reply**, not a line that gets quietly dropped. An unknown *name* is the
soft case — that one is ignored and the reply survives.

```sh
PET expr thinking      # right
PET action dance       # right
PET expression thinking  # WRONG: "agent sysusage: PET: unknown command ..."
```

Check your agent without installing it, using the same protocol reader the app
uses — global flags come *before* the subcommand:

```sh
onidia-chat/agentctl validate agents/sysusage          # manifest + exec
onidia-chat/agentctl -dir agents run sysusage           # one real run
```