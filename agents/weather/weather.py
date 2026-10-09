#!/usr/bin/env python3
"""weather - current conditions and a short forecast for a place you name.

Reads one "RUN {json}" line on stdin, geocodes the `location` string against
Open-Meteo's gazetteer, fetches the forecast for the coordinates that come
back, answers "OK <message>". Pure stdlib, no dependencies, no API key.

WHY OPEN-METEO
--------------
A downloaded agent never receives API keys - docs/AGENT-PROTOCOL.md says so
outright, because the key would then live in a user-writable folder. Open-Meteo
is the one serious forecast source that needs no account at all: public
endpoints, generous free tier, no key in the URL. That is what makes this
shippable as a folder you drop in rather than a native agent linked into the
binary (the way `read_story` is, purely because it needs the LLM's key).

Two calls, in this order: geocode the name to a coordinate, then forecast that
coordinate. The geocoder is a fuzzy match over its own gazetteer, so "Paris",
"Berlin Germany" and "1010" all resolve, and a made-up town does not - which is
why the manifest tells the model to add the country for common city names.

WHY PLAIN ASCII AND NO UNICODE BOX DRAWING
-----------------------------------------
The reply is drawn with a 5x9 bitmap font that has no glyph for U+2500-style
box characters: an unknown rune advances and draws nothing, so a pretty
markdown table would come out as blank gaps. Place names make this worse than
sysusage has to worry about - they arrive with accents ("Zurich", "Bogota"),
so every string from the network is ASCII-folded through `fold` before it can
reach the bubble.

WHY THE REPLY IS ONE LINE
-------------------------
The bubble would happily wrap a long line across its ~25 character width, but
it CANNOT be given a newline. The docs promise that every newline becomes a
page break and a two-page reply gets prev/next controls; the protocol reader in
agent/external.go does not do that - it stops scanning at the first OK-prefixed
line and drops the rest, so an "OK" with embedded newlines silently loses
everything after its first line (`sysusage section=all` loses three of its four
metrics the same way). The whole forecast therefore shares one line, split on
" | ". That reads fine, because a bubble word-wraps within its width and needs
no scrolling.

A chat bubble is ~25 characters wide at the default 456px window (3/4 of the
window, at 12px per glyph), so each day is kept to a few words. Launch with
`-w 900` for roughly 59 characters per line.
"""
import html
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Identify ourselves. Open-Meteo's fair-use notes ask for a UA with a contact,
# and a bare "Python-urllib" is what gets throttled first.
UA = "onidia-chat-weather/1.0 (agent-line-v1)"

# Per-request socket timeout. The manifest timeout_ms (20s) is the hard
# ceiling the brain enforces; these keep one slow endpoint from spending all of
# it, so a hung geocoder still leaves time for the forecast call. If the
# machine is offline both fail fast instead of sitting on the reply.
NET_TIMEOUT = 8.0

# Read at most this much of a response body. The real replies are a few KB;
# the cap is what stops a redirect to something enormous from filling memory.
MAX_BYTES = 512 * 1024

MAX_DAYS = 7

# Punctuation that is non-ASCII in practice. Newswire copy and place names are
# full of typographic quotes and dashes; each of those is a rune the font does
# not have. Mapping them first keeps the words intact, whereas dropping them
# outright would leave a dangling apostrophe mid-sentence.
PUNCT = {
    "‘": "'", "’": "'", "‚": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "―": "-",
    "…": "...", "•": "-",
}


def out(line):
    print(line, flush=True)  # the brain reads line-by-line: never buffer


def fold(text):
    """Network text -> printable ASCII, whitespace collapsed to single spaces.

    Each pass earns its place. `html.unescape` because a place name may arrive
    entity-encoded, and a literal "&amp;" on screen is worse than an accent.
    The PUNCT table because folding those to "?" loses the word. NFKD + drop
    combining marks because accents decompose that way ("Zurich" from
    "Zürich") and the mark has to be DROPPED, not encoded: ascii-encoding it
    instead turns it into a "?" and yields "Zu?rich", a hole punched through
    every accented name on screen.
    """
    if text is None:
        return ""
    text = html.unescape(str(text))
    text = "".join(PUNCT.get(ch, ch) for ch in text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text.encode("ascii", "replace").decode("ascii"))
    # A non-breaking space becomes an ordinary one in the collapse above, which
    # would leave "M.vm , ... " where the source said "M.vm, ...". Pull
    # punctuation back against the word it belongs to, whatever space character
    # it arrived as.
    return re.sub(r" +([,;:.!?%])", r"\1", text).strip()


def fetch(url):
    """GET url and return the body as bytes. Raises on any network failure."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as resp:
        return resp.read(MAX_BYTES)
def geocode(place):
    """(label, lat, lon) for the best match of `place`, or None if unknown.

    count=1 takes the single best-scoring hit so the reply never has to
    disambiguate; the manifest tells the model to pass "City, Country" when a
    bare name is ambiguous. The label is "City CC" - the country code, not the
    full name, because that is what fits beside a forecast line.
    """
    qs = urllib.parse.urlencode({
        "name": place, "count": 1, "language": "en", "format": "json",
    })
    data = json.loads(fetch(GEOCODE_URL + "?" + qs))
    results = data.get("results") or []
    if not results:
        return None
    hit = results[0]
    label = fold(hit.get("name") or place)
    where = hit.get("country_code") or hit.get("country") or hit.get("admin1") or ""
    where = fold(where)
    if where and where.lower() != label.lower():
        label = "%s %s" % (label, where)
    return label, hit.get("latitude"), hit.get("longitude")


def forecast(lat, lon, days, units):
    """Open-Meteo forecast document for a coordinate.

    The unit switch is done server-side by Open-Meteo rather than converted
    here: asking for fahrenheit returns fahrenheit, so there is no rounding
    code to get subtly wrong and no second source of truth for the numbers.
    """
    params = {
        "latitude": "%.4f" % lat,
        "longitude": "%.4f" % lon,
        "current": "temperature_2m,weather_code",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                 "precipitation_probability_max",
        "timezone": "auto",       # days roll over at local midnight, not UTC's
        "forecast_days": days,
    }
    if units == "imperial":
        params["temperature_unit"] = "fahrenheit"
    qs = urllib.parse.urlencode(params)
    return json.loads(fetch(FORECAST_URL + "?" + qs))


# WMO 4677 weather codes, as they arrive in `weather_code`, spelled to fit a
# 25-character bubble alongside a temperature range. An unknown code degrades
# to the number itself rather than to a blank, so a code Open-Meteo adds later
# shows up as something the model can read and the user can still act on.
WMO = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "ice fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    56: "icy drizzle", 57: "icy drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "icy rain", 67: "icy rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "rain showers", 81: "showers", 82: "heavy showers",
    85: "snow showers", 86: "snow showers",
    95: "thunderstorm", 96: "storm + hail", 99: "storm + hail",
}


def describe(code):
    """Short ASCII label for a WMO weather code."""
    try:
        return WMO.get(int(code), "code %s" % code)
    except (TypeError, ValueError):
        return "unknown"


def at(seq, i):
    """Element i of a daily series, or None if it is missing or short.

    Open-Meteo keys every daily field by the same date array, so a partial
    response would otherwise raise deep inside the formatting code. None just
    means "no data for that day", which the callers render as a dash.
    """
    try:
        return seq[i]
    except (IndexError, KeyError, TypeError):
        return None


def deg(value):
    """Rounded temperature as a bare number, or a dash if missing.

    The unit letter is appended by the caller rather than baked in here, so a
    range can share ONE letter ("15..20C") instead of repeating it at both
    ends ("15C..20CC", which is what this did first).
    """
    if value is None:
        return "-"
    try:
        return "%.0f" % float(value)
    except (TypeError, ValueError):
        return "-"


def chance(value):
    """Rain probability as an int, or None when the provider omitted it."""
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def weekday(iso_day):
    """'Thu' from '2026-10-04'. The day-of-week is what makes a forecast line
    readable; a bare date would not fit beside the temperature range."""
    try:
        return date.fromisoformat(str(iso_day)).strftime("%a")
    except (TypeError, ValueError):
        return "?"

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

    place = str(args.get("location", "")).strip()
    if not place:
        # The brain already enforces `required`, so reaching this means the
        # manifest and this script disagree - say so instead of guessing.
        out("ERR missing location")
        return 1

    # The brain type-checks `days` before the run, but the value still gets
    # clamped here: an out-of-range number is a question the model can answer,
    # not a reason to fail the turn.
    try:
        days = int(str(args.get("days", "3")).strip() or "3")
    except ValueError:
        out("ERR days must be a number")
        return 1
    days = max(1, min(MAX_DAYS, days))

    units = str(args.get("units", "metric")).strip().lower() or "metric"
    if units not in ("metric", "imperial"):
        out("ERR units must be metric or imperial")
        return 1
    unit = "C" if units == "metric" else "F"

    brief = str(args.get("brief", "false")).strip().lower() in (
        "1", "true", "yes", "on",
    )

    out("INFO looking up %r" % place)
    # "expr", NOT "expression": that is pet_control's INPUT vocabulary. The
    # PET wire format only accepts action | event | expr | a movement verb, and
    # a malformed directive fails the whole run rather than being dropped - so
    # "PET expression thinking" would turn the forecast into an error.
    out("PET expr thinking")   # she looks like she is checking

    try:
        spot = geocode(place)
        if spot is None:
            out("ERR no place called %r that the weather service knows - "
                "try 'City, Country'" % place)
            return 1
        label, lat, lon = spot
        data = forecast(lat, lon, days, units)
    except urllib.error.HTTPError as e:
        out("ERR weather service returned HTTP %s" % e.code)
        return 1
    except urllib.error.URLError as e:
        out("ERR cannot reach the weather service: %s" % e.reason)
        return 1
    except ValueError as e:
        # json.loads failures land here: a truncated or HTML error page.
        out("ERR weather service sent something unreadable: %s" % e)
        return 1
    except OSError as e:
        # Bare socket timeouts and DNS failures that never became URLError.
        out("ERR weather service unreachable: %s" % e)
        return 1

    current = data.get("current") or {}
    daily = data.get("daily") or {}
    days_iso = daily.get("time") or []
    codes = daily.get("weather_code") or []
    highs = daily.get("temperature_2m_max") or []
    lows = daily.get("temperature_2m_min") or []
    rains = daily.get("precipitation_probability_max") or []

    now = "%s%s" % (deg(current.get("temperature_2m")), unit)
    now_desc = describe(current.get("weather_code"))
    today = "%s..%s%s" % (deg(at(lows, 0)), deg(at(highs, 0)), unit)

    if brief:
        out("OK %s: %s %s, today %s" % (label, now, now_desc, today))
        return 0

    # ONE line, never a newline. This is the whole shape of the reply and the
    # least obvious thing in this file: the docs describe multi-line replies
    # becoming a paged bubble, but the protocol reader in agent/external.go
    # breaks out of its scan loop on the FIRST OK-prefixed line, so every line
    # after the first is dropped - `sysusage section=all` loses three of its
    # four metrics the same way. A newline here would silently show the user
    # the current conditions and no forecast at all, so the days share one
    # line, separated by " | ".
    parts = ["%s: %s %s" % (label, now, now_desc)]
    for i in range(len(days_iso)):
        rain = chance(at(rains, i))
        # "Sun 17..21C overcast 58% rain" - compact, because they share a line.
        day = "%s %s..%s%s %s" % (
            weekday(at(days_iso, i)),
            deg(at(lows, i)), deg(at(highs, i)), unit,
            describe(at(codes, i)),
        )
        if rain is not None:
            day += " %d%% rain" % rain
        parts.append(day)

    out("OK " + " | ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())

