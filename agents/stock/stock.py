#!/usr/bin/env python3
"""stock - a quote and a short history for a stock, by ticker or company name.

Reads one "RUN {json}" line on stdin, resolves `symbol` through Yahoo's
search, pulls daily bars from Yahoo's chart API, answers "OK <message>".
Pure stdlib, no dependencies, no API key - but it does need the internet.

WHY YAHOO FINANCE
-----------------
A downloaded agent never receives API keys (docs/AGENT-PROTOCOL.md), so the
source has to be public and key-less. Yahoo's chart endpoint gives everything
one reply needs in a single JSON document: the company name, the currency, the
last price, the previous close for the day change, thirty daily bars, and an
adjusted-close series so a dividend or a split does not show up as a fake
price jump. Stooq's classic CSV endpoints were tried first and 404 from here.
The price Yahoo reports is delayed about fifteen minutes, which the manifest
says outright so the model never presents it as a live tick.


TICKER SHAPES, AND WHY "Tesla" IS NOT ONE
-----------------------------------------
"Tesla" matches every regex for a ticker, but it is a company before it is a
symbol, and minting "TESLA" from it fails on the chart endpoint - no exchange
lists that. So the search runs for every query and only a clean miss falls
back to the ticker guess; prefer_exact still takes a same-symbol hit when the
typed text really is a listing. Name matches compare bags of words, not
strings: "Tesla, Inc." and "tesla" are the same words, and a leveraged fund
never wins a search for the underlying because it shares the name but not the
lead word.


WHY THE GRAPH IS ASCII, AND WHY IT IS ONE LINE
----------------------------------------------
What fits in the reply text is fixed pitch printable ASCII: the bitmap font
has no block-element glyphs, and the agent is text anyway. The history window
renders as a sparkline of adjusted closes on a small ramp (".,-~=+*#"), plus
the window change and the low..high range. It shares the single OK line with
everything else because the protocol reader in agent/external.go stops
scanning at the first OK-prefixed line - a graph on the second line would be
dropped silently, exactly like sysusage's lost metrics. The bubble word-wraps
the line, and the same font that draws the news agent's age column draws the
ramp. That ramp is the CAPTION's graph, not the reader's: TTS speaks words,
the bubble shows pixels, and each keeps to one line - or one picture.

THE CHART THE USER ACTUALLY ASKED FOR
-------------------------------------
A real chart - axes, guides, a price line - cannot be a line of text, and
the bubble already knows how to draw one: onidia/bubble.go RenderBubbleMedia
puts an image up to 260x200px above the caption, and the [IMG: ...] tag path
already separates "the picture" from "the words" in a reply. So a history
run emits "IMG png <base64>" BEFORE the OK line - the reader stops at OK,
so the picture must arrive first - and the line is optional: a chart that
will not draw costs one INFO line, never the quote. The encoder is stdlib
(zlib + struct, no PIL) because a downloaded agent may not ask the host to
install anything, the canvas is the bubble box itself (260x200) so nothing
is scaled on the way in, and history=none draws nothing at all - no window,
no picture. Bytes are not instructions, so a picture changes no part of the
trust model.

Full HTML is deliberately not the recommendation. Tables and charts do not
need a layout engine, and this app ships static Go binaries with a bitmap font
and a TTS reader. An HTML surface would add a webview to every install, break
the visual language and the spoken reply in one move, and hand every agent
process - including every downloaded zip users are told to trust - the ability
to run code in the desktop. PNG first: decode + scale + show, one throat to
choke, and the agent can never draw anything it could not already say.
"""
import base64
import json
import re
import struct
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timezone

SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart"

# Yahoo's chart API is a browser-facing endpoint that answers 401 to a bare
# script UA. The UA identifies the agent honestly; the Mozilla prefix is what
# keeps the CDN from rejecting the request at the door.
UA = ("Mozilla/5.0 (X11; Linux aarch64) "
      "onidia-chat-stock/1.0 (agent-line-v1)")

# Per-request socket timeout. The manifest timeout_ms (25s) is the hard
# ceiling the brain enforces; a resolve call plus a chart call each get a
# slice, so one hanging endpoint cannot spend the whole budget.
NET_TIMEOUT = 8.0

# A chart document is a few KB; the cap stops a redirect to something enormous
# from filling memory.
MAX_BYTES = 512 * 1024

MAX_POINTS = 30
NAME_MAX = 24

# The sparkline ramp, visually ascending. No space - it would collapse in the
# whitespace pass - and nothing the 5x9 font cannot draw; it draws every
# printable ASCII rune except the backtick.
RAMP = ".,-~=+*#"

# The chart canvas is the bubble box exactly (onidia/bubble.go
# RenderBubbleMedia): 260x200 is the largest image a reply can show, so
# nothing is scaled on the way in - and the IMG caps (800x600, 400KB
# encoded) are comfortably ahead of it.
CHART_W, CHART_H = 260, 200

# Palette on white: readable in a screenshot, printable, and honest about
# direction - green up, red down, blue for a window that went nowhere.
CHART_BG = (255, 255, 255)
CHART_INK = (24, 24, 24)        # axes, frame, labels
CHART_GRID = (214, 214, 214)    # guides, drawn under the series
CHART_UP = (0, 128, 56)
CHART_DOWN = (200, 40, 40)
CHART_FLAT = (60, 90, 200)

# A 3x5 pixel font: rows of bits, MSB leftmost. Only the runes a chart
# itself speaks - prices and dates, minus included; every other word is
# the caption's job, and an unknown rune draws as a blank.
CHART_FONT = {
    "0": (0b111, 0b101, 0b101, 0b101, 0b111),
    "1": (0b010, 0b110, 0b010, 0b010, 0b111),
    "2": (0b111, 0b001, 0b111, 0b100, 0b111),
    "3": (0b111, 0b001, 0b111, 0b001, 0b111),
    "4": (0b101, 0b101, 0b111, 0b001, 0b001),
    "5": (0b111, 0b100, 0b111, 0b001, 0b111),
    "6": (0b111, 0b100, 0b111, 0b101, 0b111),
    "7": (0b111, 0b001, 0b010, 0b010, 0b010),
    "8": (0b111, 0b101, 0b111, 0b101, 0b111),
    "9": (0b111, 0b101, 0b111, 0b001, 0b111),
    ".": (0b000, 0b000, 0b000, 0b000, 0b010),
    "-": (0b000, 0b000, 0b111, 0b000, 0b000),
    " ": (0b000, 0b000, 0b000, 0b000, 0b000),
}

# Ticker shapes Yahoo accepts, so the model can pass "AAPL", "BMW.DE" or
# "^GSPC" straight through. Company names ("Apple", "Tesla Inc") fail this
# test and take the search path instead.
SYMBOL_RE = re.compile(r"^[A-Za-z^][A-Za-z0-9.\-=^]{0,11}$")

# Listing types worth answering for. Derivatives, currencies and options match
# the same query text but are not what "what is Tesla at" means; those fall
# through to the first hit only if nothing better exists.
LISTING_TYPES = ("EQUITY", "ETF", "INDEX", "MUTUALFUND")

# Typographic punctuation that is non-ASCII in practice. Company names carry
# accents (Nestle) and news-style quotes; each is a rune the font cannot draw.
PUNCT = {
    "‘": "'", "’": "'", "‚": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "―": "-",
    "…": "...", "•": "-", "·": "-",
}


def out(line):
    print(line, flush=True)  # the brain reads line-by-line: never buffer


def fold(text):
    """Network text -> printable ASCII, whitespace collapsed to single spaces.

    Unescape nothing here (Yahoo returns clean JSON, not entity-encoded HTML),
    map the typographic punctuation to ASCII, decompose accents to their base
    letter ("Nestle" from "Nestlé") by dropping the combining mark rather than
    encoding it - encoding yields "?" and "Nestl?" is a typo, not a fold - and
    turn anything genuinely outside ASCII into "?".
    """
    if text is None:
        return ""
    text = "".join(PUNCT.get(ch, ch) for ch in text)
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text.encode("ascii", "replace").decode("ascii"))
    return re.sub(r" +([,;:.!?%])", r"\1", text).strip()


def clip(text, limit):
    """fold(), then hard-cut to `limit` chars on a word boundary. A company
    name like "Berkshire Hathaway Inc." must not eat a third of the reply."""
    text = fold(text)
    if len(text) <= limit:
        return text
    cut = text[:limit - 3].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (cut or text[:limit - 3]) + "..."


def fetch_json(url):
    """GET url and decode the JSON body. Raises on any network failure."""
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as resp:
        return json.loads(resp.read(MAX_BYTES))


def resolve(query):
    """(symbol, name) for a ticker or company name, or (None, None).

    The name check comes first: "Tesla" matches the ticker shape below, but it
    is a company before it is a symbol, and skipping the search turns it into
    "TESLA" - which no exchange lists. So the search runs whenever the input
    has a hit whose name actually resembles the query; only a clean miss falls
    through to the ticker guess. When the typed text IS the symbol, the search
    still confirms it free, and prefer_exact takes the same-symbol hit.
    """
    qs = urllib.parse.urlencode({"q": query, "quotesCount": 6, "newsCount": 0})
    try:
        data = fetch_json(SEARCH_URL + "?" + qs)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        data = None
    if data:
        hits = [h for h in (data.get("quotes") or []) if h.get("symbol")]
        exact = prefer_exact(hits, query)
        if exact is not None:
            return exact
        named = prefer_name(hits, query)
        if named is not None:
            return named
    if SYMBOL_RE.match(query):
        return query.upper(), ""
    return None, None


def tidy(name):
    """A search hit's name, cleaned: prefer the short form, strip punctuation
    so "Tesla, Inc." and "tesla" compare as the same words."""
    return re.sub(r"[^a-z0-9]+", " ", fold(name).lower()).strip()


def prefer_exact(hits, query):
    """The hit whose symbol IS the query, if its type could be an answer.

    A futures contract with a memorable ticker ("STSLA=F") does not become
    "the Tesla stock" just because the letters match; the listing filter
    applies here too, not only to fuzzy name matches.
    """
    want = query.strip().upper()
    for hit in hits:
        if str(hit.get("symbol", "")).upper() == want:
            if str(hit.get("quoteType", "")).upper() in LISTING_TYPES:
                return hit["symbol"], hit_name(hit)
    return None


def prefer_name(hits, query):
    """The best hit whose NAME says the query, listing types first.

    resemblance is all-words-mention-each-other: every word of the query must
    appear in the hit's name, and the hit's lead word must appear in the
    query. The second half is what rejects the funds - "Tesla" is not in
    "T-Rex 2X Long Tesla Daily Target", so a leveraged product never wins a
    name search, however relevant Yahoo scores it.
    """
    qw = set(tidy(query).split())
    qw.discard("inc")
    if not qw:
        return None
    listed, other = [], []
    for hit in hits:
        words = set(tidy(hit_name(hit)).split())
        if not qw <= words:
            continue
        if next(iter(words), "") not in qw and len(words - qw) > 1:
            continue
        (listed if str(hit.get("quoteType", "")).upper() in LISTING_TYPES
         else other).append(hit)
    for pool in (listed, other):
        if pool:
            hit = pool[0]
            return hit["symbol"], hit_name(hit)
    return None


def hit_name(hit):
    """A hit's display name, short form first."""
    return hit.get("shortname") or hit.get("longname") or ""


def chart(symbol, trading_days):
    """(meta, closes) for a symbol: `trading_days` daily closes at most.

    range=1mo would do for the 30d window, and range=1wk for 7d - but the
    endpoint treats "7d" as seven CALENDAR days, which is five sessions, while
    the user asked for seven sessions. The seven-day request therefore still
    asks for a month of dailies and keeps the last seven: one extra download,
    and the number on screen means what it says. The count is honest about
    what came back - a fresh IPO has fewer sessions than requested, and
    reporting that is better than inventing the missing ones.
    """
    qs = urllib.parse.urlencode({"interval": "1d", "range": "1mo"})
    data = fetch_json("%s/%s?%s" % (CHART_URL, symbol, qs))
    err = (data.get("chart") or {}).get("error")
    if err:
        raise ValueError(str(err.get("description") or err.get("code") or err))
    results = (data.get("chart") or {}).get("result") or []
    if not results:
        raise ValueError("no chart data for %r" % symbol)
    doc = results[0]
    meta = doc.get("meta") or {}
    quote = ((doc.get("indicators") or {}).get("quote") or [{}])[0]
    adj = ((doc.get("indicators") or {}).get("adjclose") or [{}])[0]
    closes = quote.get("close") or []
    adjs = adj.get("adjclose") or []
    stamps = doc.get("timestamp") or []

    # A corporate action is a dividend or a split, and raw closes lie across
    # one: the price drops overnight without anyone selling. adjclose divides
    # the break out, so it is the series the graph and the window change are
    # drawn from. The QUOTE itself stays the raw last price - that is the
    # number on the board, adjustments and all, not a revised history.
    rows = []
    for i in range(len(closes)):
        price = adjs[i] if i < len(adjs) and adjs[i] is not None else closes[i]
        if price is None:
            continue                       # halted day: a gap, not a zero
        day = ""
        if i < len(stamps):
            try:
                day = datetime.fromtimestamp(stamps[i],
                                             timezone.utc).strftime("%a %d")
            except (TypeError, ValueError, OverflowError, OSError):
                day = ""
        rows.append((day, float(price)))
    if len(rows) < 2:
        raise ValueError("only one session of history for %r" % symbol)
    if trading_days and len(rows) > trading_days:
        rows = rows[-trading_days:]
    return meta, [p for _, p in rows], [d for d, _ in rows]


def sparkline(values, width=MAX_POINTS):
    """One ASCII line of the series, oldest on the left.

    The window is downsampled to at most `width` columns so 30 closes fit one
    line: taking evenly spaced samples preserves the shape and the endpoints,
    which a moving average would smooth into a lie. A flat week renders as a
    flat middle row, not as noise - the eye reads "no move" correctly.
    """
    if not values:
        return "?"
    if len(values) > width:
        step = (len(values) - 1.0) / (width - 1.0)
        values = [values[int(round(i * step))] for i in range(width)]
    lo, hi = min(values), max(values)
    if hi <= lo:
        return "." * len(values)           # every close identical: say so
    span = float(hi - lo)
    return "".join(RAMP[min(len(RAMP) - 1,
                            int((v - lo) / span * len(RAMP)))] for v in values)


# ---- the picture -------------------------------------------------------
# Everything below is the IMG png chart: pure stdlib (a downloaded agent
# may not ask the host to install anything), sized to the bubble box, and
# never allowed to fail the run - see main().

def png_bytes(width, height, pixels):
    """Pack an RGB byte buffer (row-major, three bytes a pixel) into PNG.

    Color type 2, eight bits a channel: the plainest file every decoder
    reads, which is all the bubble's image/png needs. Each chunk is a big
    endian length, the tag, the body, and the body's CRC - zlib.crc32 is
    the CRC the spec uses, so no new dependency for one checksum.
    """
    def chunk(tag, body):
        return (struct.pack(">I", len(body)) + tag + body +
                struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff))

    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)               # filter: none - one byte, no transform
        raw += pixels[y * stride:(y + 1) * stride]
    magic = bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (magic + chunk(b"IHDR", ihdr) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 9)) +
            chunk(b"IEND", b""))


def price_mark(v, lo, hi):
    """A guide line's label. Whole numbers once the scale or the size earns
    them: 333.146666667 in a five-pixel font is noise, 333 is a reading."""
    if hi >= 1000 or (hi - lo) >= 50:
        return "%.0f" % v
    return "%.2f" % v


def day_mark(label):
    """'Mon 06' -> '06': day of month for the tick. The caption already
    said the month, and this font spells no months."""
    parts = str(label or "").split()
    if not parts:
        return ""
    day = parts[-1]
    return day if len(day) <= 2 else day[-2:]


def _plot_put(pixels, x, y, color):
    """Set one pixel, quietly clipping off-canvas - clipped labels at the
    edge are normal, exceptions mid-reply are not."""
    if 0 <= x < CHART_W and 0 <= y < CHART_H:
        i = (y * CHART_W + x) * 3
        pixels[i], pixels[i + 1], pixels[i + 2] = color


def _plot_text(pixels, x, y, text, color):
    """Draw text in CHART_FONT: three pixels a glyph, one column a gap."""
    for ch in text.upper():
        rows = CHART_FONT.get(ch, CHART_FONT[" "])
        for ry, row in enumerate(rows):
            for cx in range(3):
                if row & (4 >> cx):
                    _plot_put(pixels, x + cx, y + ry, color)
        x += 4


def _plot_hline(pixels, x0, x1, y, color):
    for x in range(x0, x1 + 1):
        _plot_put(pixels, x, y, color)


def _plot_vline(pixels, x, y0, y1, color):
    for y in range(y0, y1 + 1):
        _plot_put(pixels, x, y, color)


def _plot_line(pixels, x0, y0, x1, y1, color):
    """Bresenham - integer endpoints only, so first and last close land
    exactly where the frame says they do."""
    dx, dy = abs(x1 - x0), -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        _plot_put(pixels, x0, y0, color)
        if x0 == x1 and y0 == y1:
            return
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy


def chart_png(days, closes):
    """The history window as a 260x200 PNG: frame, three guides with their
    prices in the margin, first/middle/last dates, and the close line.

    Green when the window rose, red when it fell, blue when it went
    nowhere - the one thing a chart says faster than the caption. A flat
    window draws a middle rule instead of dividing by zero, and a 3px cap
    marks the last close: where the price IS, as against where it has
    been.
    """
    pixels = bytearray([255]) * (CHART_W * CHART_H * 3)

    left, right, top, bottom = 38, 8, 10, 18
    x0, y0 = left, top
    x1, y1 = CHART_W - right - 1, CHART_H - bottom - 1
    lo, hi = min(closes), max(closes)
    span = hi - lo

    def at_x(i):
        return x0 + int(round(i * (x1 - x0) / float(len(closes) - 1)))

    def at_y(v):
        if span <= 0:
            return (y0 + y1) // 2
        return y1 - int(round((v - lo) / span * (y1 - y0)))

    # Guides first: frame and series are drawn on top of them, and the
    # labels live in the margin where nothing can overwrite them.
    marks = [lo, hi] if span <= 0 else [lo, (lo + hi) / 2.0, hi]
    for v in marks:
        y = at_y(v)
        _plot_hline(pixels, x0, x1, y, CHART_GRID)
        _plot_text(pixels, 2, y - 2, price_mark(v, lo, hi), CHART_INK)

    _plot_hline(pixels, x0, x1, y0, CHART_INK)
    _plot_hline(pixels, x0, x1, y1, CHART_INK)
    _plot_vline(pixels, x0, y0, y1, CHART_INK)
    _plot_vline(pixels, x1, y0, y1, CHART_INK)

    change = closes[-1] - closes[0]
    color = (CHART_UP if change > 0 else
             CHART_DOWN if change < 0 else CHART_FLAT)
    for i in range(1, len(closes)):
        _plot_line(pixels, at_x(i - 1), at_y(closes[i - 1]),
                   at_x(i), at_y(closes[i]), color)
    lx, ly = at_x(len(closes) - 1), at_y(closes[-1])
    _plot_hline(pixels, lx - 1, lx + 1, ly, color)
    _plot_vline(pixels, lx, ly - 1, ly + 1, color)

    # Ticks under first, middle and last session - three dates carry the
    # window's time axis; thirty would be a picket fence.
    for i in sorted({0, len(closes) // 2, len(closes) - 1}):
        tx = at_x(i)
        _plot_vline(pixels, tx, y1 + 1, y1 + 2, CHART_INK)
        day = day_mark(days[i]) if i < len(days) else ""
        if day:
            _plot_text(pixels, tx - len(day) * 2, y1 + 5, day, CHART_INK)

    return png_bytes(CHART_W, CHART_H, pixels)


def money(value, currency):
    """A price with its unit, or a dash when Yahoo left the field out."""
    if value is None:
        return "-"
    try:
        return "%.2f %s" % (float(value), currency or "")
    except (TypeError, ValueError):
        return "-"


def signed_pct(new, old):
    """'+1.24%' change from old to new, or '' when either side is missing."""
    try:
        old, new = float(old), float(new)
    except (TypeError, ValueError):
        return ""
    if old == 0:
        return ""
    return "%+.2f%%" % (100.0 * (new - old) / old)


def main():
    line = sys.stdin.readline()
    if not line.startswith("RUN "):
        out("ERR protocol: expected a RUN line")
        return 1
    try:
        args = json.loads(line[4:])
    except ValueError as e:
        out("ERR bad RUN payload: %s" % e)
        return 1

    query = fold(args.get("symbol"))
    if not query:
        out("ERR missing symbol")
        return 1

    history = str(args.get("history", "7d")).strip().lower() or "7d"
    if history not in ("none", "7d", "30d"):
        out("ERR history must be none, 7d or 30d")
        return 1
    want = {"none": 0, "7d": 7, "30d": 30}[history]

    out("INFO looking up %r" % query)
    # "expr", NOT "expression": pet_control's INPUT vocabulary. A malformed
    # PET directive fails the whole run rather than being dropped.
    out("PET expr thinking")   # she looks like she is checking

    sym = name = None
    shown = query

    try:
        symbol, sname = resolve(query)
        if symbol is None:
            out("ERR nothing on any exchange answers to %r - try the ticker, "
                "e.g. AAPL for Apple" % query)
            return 1
        meta, closes, days = chart(symbol, want or 30)
        shown = symbol                      # the reply says what actually ran
    except urllib.error.HTTPError as e:
        if str(e.code) == "404":
            out("ERR no quote for %r - the ticker exists on no exchange the "
                "feed knows; check the symbol" % (shown or query))
            return 1
        out("ERR quote service returned HTTP %s" % e.code)
        return 1
    except urllib.error.URLError as e:
        out("ERR cannot reach the quote service: %s" % e.reason)
        return 1
    except ValueError as e:
        out("ERR %s" % e)
        return 1
    except OSError as e:
        out("ERR quote service unreachable: %s" % e)
        return 1

    name = clip(sname or meta.get("longName") or meta.get("shortName") or "",
                NAME_MAX)
    currency = fold(meta.get("currency"))
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose")
    if price is None and closes:
        price = closes[-1]                 # after hours: last session's close
    if prev is None and len(closes) > 1:
        prev = closes[-2]

    head = "%s %s" % (fold(symbol), money(price, currency))
    day = signed_pct(price, prev)
    if day:
        head += " %s today" % day
    if name:
        head += " (%s)" % name

    if not want:
        # history=none is the quote alone: no window, so no trend line and
        # no chart either - the picture mirrors the tail exactly.
        out("OK " + head)
        return 0

    # The history tail shares the one OK line - it has to, because the reader
    # keeps only the first OK-prefixed line. A bare ramp reads as line noise,
    # so the tail names itself ("trend"), pins a real price to each end of the
    # ramp with '>' as the time arrow, and carries the window change: the
    # shape between two numbers is a chart, a run of glyphs is not.
    first, last = closes[0], closes[-1]
    tail = "trend %s over %dd: %.2f>%s>%.2f" % (
        signed_pct(last, first) or "?",
        len(closes), first, sparkline(closes), last)
    # The picture goes above the OK line - the reader stops at OK - and it
    # is optional: art that will not draw costs one INFO line, never the
    # quote the user asked for.
    try:
        art = chart_png(days, closes)
        out("IMG png " + base64.b64encode(art).decode("ascii"))
    except Exception as e:
        out("INFO chart skipped: %s" % e)
    out("OK %s | %s" % (head, tail))
    return 0


if __name__ == "__main__":
    sys.exit(main())

