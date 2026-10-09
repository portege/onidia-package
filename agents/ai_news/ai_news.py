#!/usr/bin/env python3
"""ai_news - the latest headlines on AI (or any topic you name).

Reads one "RUN {json}" line on stdin, fetches a news feed for `topic`, answers
"OK <message>". Pure stdlib, no dependencies, no API key.

WHY TWO FEEDS
-------------
Google News RSS is the primary: it aggregates mainstream and trade press, so
"latest AI news" comes back as journalism rather than as link aggregation.
Hacker News' public Algolia API is the fallback, for when Google is
rate-limiting, unreachable, or has nothing for a niche topic. Each source is
tried in turn and the first one that yields a usable headline wins; if all of
them fail the run ends with ERR rather than an empty OK, so a silent "no news"
never masquerades as "there is no news".

Both are public and key-less, for the same reason weather uses Open-Meteo: a
downloaded agent never gets an API key, because the key would then sit in a
user-writable folder (docs/AGENT-PROTOCOL.md).

WHY FRESHNESS IS SHOWN
----------------------
"Asking for the latest news" is the whole point, so each headline carries how
old it is. An undated headline list cannot answer "is this actually news?" -
the same three stories can sit at the top of a feed for a fortnight. The
default query is narrowed with Google's `when:7d` operator and the ages are
computed locally from the feed's own timestamps, so a week-old item is visible
as a week-old item instead of looking current.

WHY PLAIN ASCII AND NO UNICODE BOX DRAWING
-----------------------------------------
The reply is drawn with a 5x9 bitmap font that has no glyph outside printable
ASCII, and an unknown rune advances while drawing NOTHING. This bites harder
here than anywhere else: news copy is full of curly quotes, em dashes and
non-breaking spaces, and each of those would punch a blank hole through the
middle of a word rather than raise an error. Everything from the network
therefore goes through `fold`, which maps the common typographic characters to
ASCII, decomposes accents, and turns whatever is left into "?". HTML entities
are unescaped first, because feeds encode apostrophes and ampersands.

WHY THE REPLY IS ONE LINE
-------------------------
The docs say every newline in a reply becomes a form-feed page break and a
two-page reply gets prev/next controls. The protocol reader in
agent/external.go does not implement that: it stops scanning at the first
OK-prefixed line and drops everything after it, so an "OK" carrying embedded
newlines silently loses all but its first line. For a news list that is the
worst possible bug - the user would get one headline and no idea that four
more had been thrown away. The headlines therefore share a single line, split
on " | ", which the bubble word-wraps across its ~25 character width without
needing a page at all. `brief = true` returns just the newest one.
"""
import html
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

GOOGLE_NEWS = "https://news.google.com/rss/search"
HN_ALGOLIA = "https://hn.algolia.com/api/v1/search_by_date"

UA = "onidia-chat-ai_news/1.0 (agent-line-v1)"

# Per-request socket timeout. The manifest timeout_ms (25s) is the hard ceiling
# the brain enforces; with two feeds to try, each gets a slice of it so one
# hanging endpoint cannot spend the whole budget before the fallback runs.
NET_TIMEOUT = 7.0
MAX_BYTES = 2 * 1024 * 1024   # a busy feed page is a few hundred KB

# Caps. `count` is already limited to 10 by the manifest; these bound what the
# *feeds* hand back regardless, so a generous count can never pull a 1000-item
# list into memory to slice five from.
MAX_HEADLINES = 10
MAX_AGE_SECONDS = 8 * 86400   # ignore anything older than a week

# Headlines are clipped so one runaway title cannot fill a whole bubble. Long
# enough that a real headline is not truncated mid-thought in the common case.
TITLE_MAX = 72

PUNCT = {
    "‘": "'", "’": "'", "‚": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "―": "-",
    "…": "...", "•": "-", "·": "-",
}


def out(line):
    print(line, flush=True)  # the brain reads line-by-line: never buffer


def fold(text):
    """Feed text -> printable ASCII, whitespace collapsed to single spaces.

    Unescape first (feeds entity-encode apostrophes and ampersands, and a
    literal "&amp;" on screen is worse than a stray accent), map the
    typographic punctuation that would otherwise turn into "?", then decompose
    accents to ASCII ("Zürich" -> "Zurich"). The decomposed combining mark is
    DROPPED rather than encoded, because ascii-encoding it yields a "?" and
    turns every accented word into a typo. Anything genuinely outside ASCII
    (CJK, emoji) becomes "?" rather than a silent blank on screen.
    """
    if text is None:
        return ""
    text = html.unescape(str(text))
    text = "".join(PUNCT.get(ch, ch) for ch in text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text.encode("ascii", "replace").decode("ascii"))
    # A non-breaking space becomes an ordinary one in the collapse above, which
    # leaves "AI ," where the source said "AI,". Pull punctuation back against
    # the word it belongs to, whatever space character it arrived as.
    return re.sub(r" +([,;:.!?%])", r"\1", text).strip()


def clip(text, limit=TITLE_MAX):
    """fold(), then hard-cut to `limit` characters on a word boundary."""
    text = fold(text)
    if len(text) <= limit:
        return text
    cut = text[:limit - 3].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (cut or text[:limit - 3]) + "..."


def fetch(url):
    """GET url and return the body as bytes. Raises on any network failure."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as resp:
        return resp.read(MAX_BYTES)
def age_label(seconds):
    """Right-aligned age token: ' 45m', '  2h', '  3d'.

    Fixed width on purpose: the agent then renders as "[  2h] headline", and
    every headline in the bubble starts its text at the same column, which is
    the same reason sysusage's meters are a fixed '#'/'-' width.
    """
    if seconds is None:
        return "?"
    if seconds < 3600:
        return "%3dm" % max(1, int(round(seconds / 60.0)))
    if seconds < 86400:
        return "%3dh" % int(round(seconds / 3600.0))
    return "%3dd" % int(round(seconds / 86400.0))


def _to_epoch(dt):
    """A datetime from a feed to epoch seconds, assuming UTC when naive.

    Feeds do send timestamps without an offset. Interpreting those as local
    time would make "2h ago" wrong by the machine's UTC offset, which is
    exactly the kind of quiet inaccuracy this agent exists to avoid, so naive
    timestamps are read as UTC.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def parse_rfc2822(stamp):
    """Google's RFC 2822 pubDate -> epoch seconds, or None if unparseable."""
    if not stamp:
        return None
    try:
        return _to_epoch(parsedate_to_datetime(str(stamp)))
    except (TypeError, ValueError):
        return None


def parse_iso(stamp):
    """HN's ISO 8601 created_at -> epoch seconds, or None if unparseable.

    The trailing "Z" is rewritten to "+00:00" first: fromisoformat only learned
    to accept "Z" in Python 3.11, and this agent should not care which 3.x the
    chat window happens to run on.
    """
    if not stamp:
        return None
    try:
        return _to_epoch(datetime.fromisoformat(str(stamp).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def domain_of(url):
    """Host part of a URL, or "". A bare domain reads better beside a headline
    than a full link, and a malformed one must not cost us the whole item."""
    url = fold(url)
    if "://" not in url:
        return ""
    host = url.split("://", 1)[1].split("/", 1)[0]
    return host.split("@")[-1].split(":")[0]


def google_news(topic):
    """[(title, source, epoch)] from the Google News RSS feed.

    Google titles arrive as "Headline - Publisher", so the split is on the
    LAST " - ": a headline that contains a dash of its own keeps it, and only
    the trailing publisher is peeled off.
    """
    qs = urllib.parse.urlencode({
        "q": "%s when:7d" % topic,
        "hl": "en-US", "gl": "US", "ceid": "US:en",
    })
    root = ElementTree.fromstring(fetch(GOOGLE_NEWS + "?" + qs))
    items = []
    for item in root.findall(".//item")[:MAX_HEADLINES * 4]:
        title = fold(item.findtext("title"))
        if not title:
            continue
        head, sep, tail = title.rpartition(" - ")
        source = tail.strip() if (sep and tail.strip()) else ""
        if sep and source:
            title = head.strip()
        items.append((title, source, parse_rfc2822(item.findtext("pubDate"))))
    return items


# Words too common to count as evidence that a story is on-topic. Hacker
# News' search covers the comment text as well as the title, so an off-topic
# story can match "AI" somewhere in its body - the title filter below is what
# keeps the fallback from drifting into whatever is merely popular.
STOPWORDS = {
    "an", "the", "of", "in", "on", "for", "and", "to", "at", "by", "with",
    "from", "about", "latest", "news", "is", "are", "was", "were", "has",
    "have", "what", "who", "how", "why", "tell",
}


def topic_tokens(topic):
    """Significant lowercase words from a topic, for the title filter."""
    words = re.findall(r"[a-z0-9]+", fold(topic).lower())
    return [w for w in words if len(w) > 1 and w not in STOPWORDS]


def on_title(title, tokens):
    """True when every token appears in the title, case-insensitively.

    All of them, not any: a two-word topic like "AI regulation" should not be
    satisfied by a story that only happens to mention regulation. Applied to
    Hacker News only - Google News already ranks on real relevance, and a naive
    substring test there would throw away stories that use the right word in a
    form the test does not see.
    """
    if not tokens:
        return True
    low = title.lower()
    return all(token in low for token in tokens)


def hacker_news(topic):
    """[(title, source, epoch)] from Hacker News' public Algolia search.

    tags=story keeps out Ask HN threads and comments, which match the query
    just as well but are not news. The API needs no key and has no rate limit
    worth worrying about for one reply.
    """
    qs = urllib.parse.urlencode({
        "query": topic, "tags": "story", "hitsPerPage": MAX_HEADLINES * 4,
    })
    data = json.loads(fetch(HN_ALGOLIA + "?" + qs))
    tokens = topic_tokens(topic)
    items = []
    for hit in data.get("hits") or []:
        title = fold(hit.get("title") or hit.get("story_title"))
        if not title or not on_title(title, tokens):
            continue
        items.append((title, domain_of(hit.get("url")) or "Hacker News",
                      parse_iso(hit.get("created_at"))))
    return items


def collect(topic, want, now):
    """Newest `want` usable headlines, newest first, or [] if no source has any.

    A source counts as usable only if it yields at least one headline that
    survived the age and duplicate filters. That is what makes the fallback
    real: a feed that answers with 200 items from last month is a failure, and
    the next source should get its turn instead of the user seeing stale
    headlines described as the latest.

    Duplicates are collapsed on the first 40 characters, case-folded - feeds
    routinely carry the same wire story under three different outlets, and a
    bubble with the same headline three times is worse than a shorter list.
    """
    for source in (google_news, hacker_news):
        try:
            raw = source(topic)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                ElementTree.ParseError, ValueError, AttributeError):
            # One dead or malformed feed must cost us that feed, not the run.
            # AttributeError is here for a JSON body that parsed into a list
            # instead of an object, which is exactly what a captive portal or
            # an error page served as JSON 200 looks like.
            continue
        picked, seen = [], set()
        for title, name, stamp in raw:
            age = None if stamp is None else now - stamp
            if age is not None and age > MAX_AGE_SECONDS:
                continue
            key = title.lower()[:40]
            if key in seen:
                continue
            seen.add(key)
            picked.append((title, name, age))
        if not picked:
            continue
        # Newest first, so the sort key is the age ASCENDING. The -age that
        # looks like the obvious choice is the opposite: it sorts the oldest
        # item to the top, and "latest news" then returns a week-old story.
        # Undated items sort last rather than pretending to be freshest.
        picked.sort(key=lambda item: (1, 0) if item[2] is None else (0, item[2]))
        return picked[:want]
    return []


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

    topic = fold(args.get("topic"))
    if not topic:
        topic = "artificial intelligence"   # the manifest default, restated

    # The brain type-checks `count`, but an out-of-range number is a question
    # the model can answer rather than a reason to fail the turn.
    try:
        want = int(str(args.get("count", "5")).strip() or "5")
    except ValueError:
        out("ERR count must be a number")
        return 1
    want = max(1, min(MAX_HEADLINES, want))

    brief = str(args.get("brief", "false")).strip().lower() in (
        "1", "true", "yes", "on",
    )

    out("INFO fetching %d headline(s) about %r" % (want, topic))
    # "expr", NOT "expression": that is pet_control's INPUT vocabulary. The PET
    # wire format only accepts action | event | expr | a movement verb, and a
    # malformed directive fails the whole run rather than being dropped.
    out("PET expr thinking")   # she looks like she is reading

    now = datetime.now(timezone.utc).timestamp()
    headlines = collect(topic, want, now)
    if not headlines:
        # Deliberately not an empty OK: "no headlines" would be indistinguishable
        # from "nothing is happening", and the model would pass that on as news.
        out("ERR no recent headlines for %r - both feeds failed or had nothing "
            "from the last week" % topic)
        return 1

    title, source, age = headlines[0]
    if brief:
        where = " (%s)" % clip(source, 24) if source else ""
        out("OK %s - %s%s" % (clip(topic, 28), clip(title, 56), where))
        return 0

    # ONE line, never a newline. Same trap as the weather agent: the docs
    # promise a newline becomes a page break and the bubble gets prev/next
    # controls, but the protocol reader in agent/external.go stops scanning at
    # the FIRST OK-prefixed line and discards the rest. A newline here would
    # show the user the newest headline and quietly drop the other four, which
    # is the worst possible failure for a "give me the news" request. So the
    # headlines share one line, split on " | ", and the bubble word-wraps it.
    parts = []
    for title, source, age in headlines:
        where = " (%s)" % clip(source, 24) if source else ""
        parts.append("[%s] %s%s" % (age_label(age), clip(title), where))
    out("OK " + " | ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())

