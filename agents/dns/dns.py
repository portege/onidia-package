#!/usr/bin/env python3
"""dns - what does this domain resolve to: every DNS record type, no deps.

Reads one "RUN {json}" line on stdin, sends a UDP query to the system's
resolver, answers "OK <message>". Pure stdlib (socket + struct), no API key,
no root, no external tools - only UDP port 53 to a resolver from
/etc/resolv.conf (or the server= override).

WHY A HAND-ROLLED DNS CLIENT
----------------------------
socket.getaddrinfo answers A and AAAA only, through libc, with no TTL and no
control over the server. "What is the MX of example.com", "show me the
nameservers", "read the TXT records" are each one 16-bit number on the wire
(RFC 1035), so this speaks the protocol directly: one header, one question,
compression-aware answer parsing. Types it cannot pretty-print (DNSKEY,
NAPTR, ...) still answer as a byte count - "all record types" means the
query type is free (mnemonic or number), not that every type has a custom
renderer.

WHY SOME REPLIES ARE PAGED, NOT ONE LINE
----------------------------------------
The reader in agent/external.go stops scanning at the first OK line, so a
newline inside the message is silently lost (agents/README.md rule 2). A
raw form-feed byte is different: it is not a line break, the scanner passes
it through inside the OK line, and both the chat window (ui.go splitPages)
and the buddy bubble (onidia/bubble_page.go paginate) render it as an
explicit page break with prev/next controls. A single-type lookup is one
short line; type=all puts one record type per page, which is what keeps a
five-type overview readable in a 22-character bubble.

WHY PLAIN ASCII AND NO UNICODE BOX DRAWING
------------------------------------------
Same as every agent here: the reply is drawn by a 5x9 bitmap font with no
U+2500 box glyphs, so sections are plain "TYPE values" lines. TXT records
arrive from the outside world and go through fold() (copied from weather/
per agents/README.md) before they can reach the bubble.
"""
import html
import json
import random
import re
import socket
import struct
import sys
import unicodedata

# Per-server UDP wait. The manifest timeout_ms (30s) is the hard ceiling the
# brain enforces; these keep one dead resolver from spending all of it, so a
# silent server still leaves time for the next one in /etc/resolv.conf.
NET_TIMEOUT = 3.0
MAX_BYTES = 2048       # UDP reply size we ask for
MAX_VALS = 4           # values printed per record type before "+N more"
TXT_W = 54             # TXT values are prose: clip them to bubble width

# The type=all overview: the five record types a user means when they say
# "show me the DNS records" without naming one.
ALL_TYPES = ("A", "AAAA", "MX", "NS", "TXT")

# IANA type numbers we accept by name (case-insensitive). Any NUMBER is also
# accepted (1-65535) and rendered as "<N B>" when no pretty-printer exists.
TYPES = {
    "A": 1, "NS": 2, "CNAME": 5, "SOA": 6, "PTR": 12, "MX": 15, "TXT": 16,
    "AAAA": 28, "SRV": 33, "NAPTR": 35, "DS": 43, "DNSKEY": 48, "TLSA": 52,
    "SVCB": 64, "HTTPS": 65, "ANY": 255, "CAA": 257,
}

RCODES = {1: "format error", 2: "server failure", 4: "not implemented",
          5: "refused by server"}

# Punctuation that is non-ASCII in practice (copied from weather/). TXT
# records and PTR names carry typographic characters; each is a rune the
# bitmap font cannot draw.
PUNCT = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"',
    "\u2013": "-", "\u2014": "-", "\u2015": "-",
    "\u2026": "...", "\u2022": "-",
}


def out(line):
    print(line, flush=True)  # the brain reads line-by-line: never buffer


def fold(text):
    """Network text -> printable ASCII, whitespace collapsed (from weather/)."""
    if text is None:
        return ""
    text = html.unescape(str(text))
    text = "".join(PUNCT.get(ch, ch) for ch in text)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"\s+", " ", text.encode("ascii", "replace").decode("ascii"))
    return re.sub(r" +([,;:.!?%])", r"\1", text).strip()


def clip(text, width):
    """Fit one value into a bubble column."""
    if len(text) <= width:
        return text
    return text[:width - 3] + "..."


def resolvers():
    """Server IPs to try, in order, from /etc/resolv.conf.

    The first two nameservers only: more than that spends the run on dead
    servers. The public fallbacks cover a machine with a broken/empty
    resolv.conf - a DNS agent with no DNS is nothing at all.
    """
    servers = []
    try:
        with open("/etc/resolv.conf") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[0] == "nameserver":
                    if parts[1] not in servers:
                        servers.append(parts[1])
                    if len(servers) == 2:
                        break
    except OSError:
        pass
    return servers or ["8.8.8.8", "1.1.1.1"]


def is_ip(text):
    """True for a literal IPv4 or IPv6 address."""
    for fam in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(fam, text)
            return True
        except (OSError, ValueError):
            pass
    return False


def is_type_ip(text, qtype):
    """True when text is a literal address OF THE REQUESTED FAMILY - used to
    tell a final A/AAAA answer apart from a CNAME alias in the same list."""
    try:
        socket.inet_pton(socket.AF_INET if qtype == 1 else socket.AF_INET6, text)
        return True
    except (OSError, ValueError):
        return False


def reverse_ptr(ip):
    """The in-addr.arpa / ip6.arpa name whose PTR record is `ip`'s hostname."""
    try:
        socket.inet_pton(socket.AF_INET, ip)
        return ".".join(reversed(ip.split("."))) + ".in-addr.arpa"
    except (OSError, ValueError):
        pass
    try:
        nibbles = socket.inet_pton(socket.AF_INET6, ip).hex()
        return ".".join(reversed(nibbles)) + ".ip6.arpa"
    except (OSError, ValueError):
        raise ValueError("not an IP address: %r" % ip)


def encode_name(name):
    """Wire-format QNAME. IDNA first: the wire speaks ASCII only, so a user
    typing the real Unicode domain still reaches the right zone."""
    try:
        ascii_name = name.encode("idna").decode("ascii")
    except (UnicodeError, LookupError):
        ascii_name = name  # already punycode/ASCII, or a hopeless input
    wire = b""
    for label in ascii_name.rstrip(".").split("."):
        if not label or len(label) > 63:
            raise ValueError("bad domain %r" % name)
        wire += bytes([len(label)]) + label.encode("ascii", "replace")
    return wire + b"\x00"


def decode_name(data, off):
    """(name, next_off) following RFC 1035 compression pointers.

    next_off is the offset AFTER this name in the record it starts in; a
    pointer jumps elsewhere but the record's own cursor only advances past
    the pointer. A loop guard keeps a hostile packet from spinning forever.
    """
    labels = []
    end = None
    seen = set()
    while off < len(data):
        if off in seen:
            raise ValueError("compression loop in reply")
        seen.add(off)
        n = data[off]
        if n == 0:
            off += 1
            # The root name is "." (a null MX target, RFC 7505) - never "".
            return (".".join(labels) or "."), (end if end is not None else off)
        if n & 0xC0 == 0xC0:                       # 14-bit pointer
            if off + 1 >= len(data):
                raise ValueError("truncated name pointer")
            if end is None:
                end = off + 2
            off = ((n & 0x3F) << 8) | data[off + 1]
        elif n & 0xC0:
            raise ValueError("reserved label length bits")
        else:
            labels.append(data[off + 1:off + 1 + n].decode("ascii", "replace"))
            off += 1 + n
    raise ValueError("truncated name")


def format_rdata(data, start, length, qtype):
    """One RDATA as display text. Names inside RDATA may be compressed, so
    the whole packet is passed for decode_name. Types without a renderer
    degrade to a byte count instead of failing the lookup."""
    end = start + length
    try:
        if qtype == 1 and length == 4:                          # A
            return socket.inet_ntoa(data[start:end])
        if qtype == 28 and length == 16:                        # AAAA
            return socket.inet_ntop(socket.AF_INET6, data[start:end])
        if qtype in (2, 5, 12):                                 # NS CNAME PTR
            return decode_name(data, start)[0]
        if qtype == 15 and length > 2:                          # MX
            pref = struct.unpack_from(">H", data, start)[0]
            return "%d %s" % (pref, decode_name(data, start + 2)[0])
        if qtype == 6:                                          # SOA
            mname, off = decode_name(data, start)
            rname, off = decode_name(data, off)
            serial = struct.unpack_from(">I", data, off)[0]
            return "%s %s serial %d" % (mname, rname, serial)
        if qtype == 16:                                         # TXT
            chunks = []
            off = start
            while off < end:
                n = data[off]
                chunks.append(data[off + 1:off + 1 + n].decode("utf-8", "replace"))
                off += 1 + n
            return clip(fold(" ".join(chunks)), TXT_W)
        if qtype == 33 and length > 6:                          # SRV
            prio, weight, port = struct.unpack_from(">HHH", data, start)
            target = decode_name(data, start + 6)[0]
            return "%d %d %d %s" % (prio, weight, port, target)
        if qtype == 257 and length >= 2:                        # CAA
            flags = data[start]
            taglen = data[start + 1]
            tag = data[start + 2:start + 2 + taglen].decode("ascii", "replace")
            value = data[start + 2 + taglen:end].decode("utf-8", "replace")
            return "%d %s %s" % (flags, tag, fold(value))
    except (ValueError, IndexError, struct.error):
        return "<%d B>" % length
    return "<%d B>" % length


def build_packet(tid, name, qtype, edns):
    """One-question query packet; edns adds an OPT record (RFC 6891)
    advertising a 1232-byte UDP buffer - the modern floor, and what keeps
    fat TXT answers from arriving truncated."""
    pkt = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 1 if edns else 0)
    pkt += encode_name(name) + struct.pack(">HH", qtype, 1)
    if edns:
        # root name, TYPE=OPT(41), CLASS=1232 (payload size), TTL=0, RDLEN=0
        pkt += b"\x00" + struct.pack(">HHIH", 41, 1232, 0, 0)
    return pkt


def exchange(server, pkt, timeout, tcp=False):
    """Send one packet, return the reply bytes. UDP by default; TCP for the
    two-byte length-prefixed full response when the UDP one came back
    truncated (TC bit)."""
    if tcp:
        with socket.create_connection((server, 53), timeout) as s:
            s.sendall(struct.pack(">H", len(pkt)) + pkt)
            data = b""
            while len(data) < 2:
                chunk = s.recv(2 - len(data))
                if not chunk:
                    raise ValueError("short TCP reply")
                data += chunk
            need = struct.unpack(">H", data[:2])[0]
            while len(data) - 2 < need:
                chunk = s.recv(need - (len(data) - 2))
                if not chunk:
                    raise ValueError("short TCP reply")
                data += chunk
            return data[2:2 + need]
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.settimeout(timeout)
        sock.sendto(pkt, (server, 53))
        data, _ = sock.recvfrom(MAX_BYTES)
        return data
    finally:
        sock.close()


def parse_reply(data, tid):
    """(flags, answers) from a reply packet; raises on mismatch/format."""
    if len(data) < 12:
        raise ValueError("short reply")
    rtid, flags, qd, an, _, _ = struct.unpack_from(">HHHHHH", data)
    if rtid != tid:
        raise ValueError("reply id mismatch")
    off = 12
    for _ in range(qd):
        _, off = decode_name(data, off)
        off += 4
    answers = []
    for _ in range(an):
        _, off = decode_name(data, off)
        rtype, _, ttl, rlen = struct.unpack_from(">HHIH", data, off)
        off += 10
        answers.append((ttl, format_rdata(data, off, rlen, rtype)))
        off += rlen
    return flags, answers


def query(server, name, qtype, timeout=NET_TIMEOUT):
    """(flags, [(ttl, text), ...]) for one question. Raises on network or
    format failure so the caller can try the next server.

    Two quiet adaptations, each invisible to the caller: an ancient
    resolver that FORMERRs on the EDNS record gets the bare packet again;
    a reply that still arrives truncated (TC) is re-fetched over TCP."""
    tid = random.randint(0, 0xFFFF)
    pkt = build_packet(tid, name, qtype, edns=True)
    flags, answers = parse_reply(exchange(server, pkt, timeout), tid)
    if (flags & 0xF) == 1:  # FORMERR: almost always "no EDNS here"
        flags, answers = parse_reply(
            exchange(server, build_packet(tid, name, qtype, edns=False), timeout),
            tid)
    if flags & 0x0200:      # TC: UDP buffer capped - re-fetch over TCP. If the
        # network blocks port 53 (some ISPs do), keep the truncated UDP
        # answer rather than failing a lookup that half-worked.
        try:
            flags, answers = parse_reply(
                exchange(server, build_packet(tid, name, qtype, edns=False),
                         timeout, tcp=True), tid)
        except (OSError, ValueError):
            pass
    return flags, answers



def ask(servers, qname, qtype):
    """(flags, answers) from the first server that answers at all."""
    last = None
    for server in servers:
        try:
            return query(server, qname, qtype)
        except (OSError, ValueError) as e:
            last = e  # timeout, unreachable, malformed: try the next server
    raise last


def fmt_ttl(seconds):
    """TTL as a bubble token: 300 -> 5m, 86400 -> 1d."""
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm" % (seconds // 60)
    if seconds < 86400:
        return "%dh" % (seconds // 3600)
    return "%dd" % (seconds // 86400)


def vals_text(answers):
    """Answers as one display run: up to MAX_VALS values, then a counter."""
    vals = [t for _, t in answers]
    text = ", ".join(vals[:MAX_VALS])
    if len(vals) > MAX_VALS:
        text += " +%d more" % (len(vals) - MAX_VALS)
    return text


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

    name = str(args.get("name", "")).strip().rstrip(".")
    if not name:
        out("ERR name is required")
        return 1
    # A URL pasted by the model ("https://example.com/page") has no place on
    # the wire; the manifest tells the model to strip it, this catches the rest.
    if "://" in name:
        name = name.split("://", 1)[1].split("/", 1)[0]
    want = str(args.get("type", "A")).strip().upper() or "A"
    server_opt = str(args.get("server", "")).strip()

    if server_opt:
        try:
            socket.inet_aton(server_opt)  # IPv4 literal; the wire path is v4
        except OSError:
            out("ERR server must be an IPv4 address, got %r" % server_opt)
            return 1
        servers = [server_opt]
    else:
        servers = resolvers()

    # An IP address asked about with the default type is a reverse lookup:
    # "what is 8.8.8.8" means its PTR, not an A record for the literal.
    if is_ip(name) and want == "A":
        want = "PTR"

    if want == "ALL":
        qtypes = [(t, TYPES[t]) for t in ALL_TYPES]
    elif want in TYPES:
        qtypes = [(want, TYPES[want])]
    elif want.isdigit() and 1 <= int(want) <= 65535:
        qtypes = [("TYPE" + want, int(want))]
    else:
        out("ERR unknown record type %r - use a name (A, AAAA, MX, NS, TXT, "
            "CNAME, SOA, PTR, SRV, CAA, ...) or its number" % want)
        return 1

    try:
        qname = reverse_ptr(name) if want == "PTR" and is_ip(name) else name
    except ValueError as e:
        out("ERR %s" % e)
        return 1

    out("INFO %s %s via %s" % (qname, want, servers[0]))
    # "expr", NOT "expression": that is pet_control's INPUT vocabulary. The PET
    # wire format only accepts action | event | expr | a movement verb, and a
    # malformed directive fails the whole run rather than being dropped - so
    # "PET expression thinking" turns the lookup into an error.
    out("PET expr thinking")   # she looks like she is checking

    head = fold(name)
    via = None
    try:
        if len(qtypes) == 1:
            flags, answers = ask(servers, qname, qtypes[0][1])
            # Chase CNAMEs when the question is for an address: "www.foo.com"
            # answers with the alias chain's end, and "A some.host.name" would
            # read as a missing IP. libc follows the chain; so do we (5 hops).
            if qtypes[0][1] in (1, 28):
                for _ in range(5):
                    alias = next((t for _, t in answers
                                  if not is_type_ip(t, qtypes[0][1])), None)
                    if alias is None:
                        break
                    _, more = ask(servers, alias, qtypes[0][1])
                    if not more:
                        break      # dangling CNAME: keep the chain end
                    via = alias
                    answers = more
        else:
            parts = []             # (type, body) in query order
            dead = 0
            for typ, num in qtypes:
                try:
                    flags, answers = ask(servers, qname, num)
                except (OSError, ValueError):
                    # One slow or filtered type must not sink the whole
                    # report; only all-five-dead means the network is.
                    dead += 1
                    if dead == len(qtypes):
                        out("ERR no answer from %s" % ", ".join(servers))
                        return 1
                    parts.append((typ, "(no answer)"))
                    continue
                rcode = flags & 0xF
                if rcode == 3:  # NXDOMAIN is the same for every type: stop
                    out("ERR no such name: %s" % head)
                    return 1
                if rcode != 0:
                    out("ERR %s: %s" % (head, RCODES.get(rcode, "rcode %d" % rcode)))
                    return 1
                if answers or (flags & 0x0200):
                    body = vals_text(answers)
                    if flags & 0x0200:  # TC: truncated, even after the TCP try
                        body = (body + " (truncated)").strip()
                    parts.append((typ, body))
            if not parts:
                out("OK %s: no %s records" % (head, "/".join(ALL_TYPES)))
                return 0
            sections = []
            for i, (typ, body) in enumerate(parts):
                sections.append(("%s %s %s" % (head, typ, body)) if i == 0
                                else ("%s %s" % (typ, body)))
            # ONE OK line: the reader stops at its end. The \f bytes inside
            # it are page breaks, not newlines - see the module docstring.
            out("OK " + "\f".join(sections))
            return 0
    except (OSError, ValueError) as e:
        out("ERR no answer from %s: %s" % (", ".join(servers), e))
        return 1

    rcode = flags & 0xF
    if rcode == 3:
        out("ERR no such name: %s" % head)
        return 1
    if rcode != 0:
        out("ERR %s: %s" % (head, RCODES.get(rcode, "rcode %d" % rcode)))
        return 1
    if not answers:
        if flags & 0x0200:  # truncated to nothing: not the same as "no records"
            out("OK %s: %s records did not survive the trip (truncated by the "
                "resolver - try again with server=1.1.1.1)"
                % (head, qtypes[0][0]))
            return 0
        out("OK %s: no %s records" % (head, qtypes[0][0]))
        return 0

    text = "%s %s %s" % (head, qtypes[0][0], vals_text(answers))
    if via and all(is_type_ip(t, qtypes[0][1]) for _, t in answers):
        text += " (via %s)" % fold(via)
    ttls = {ttl for ttl, _ in answers}
    if len(ttls) == 1:
        text += " (ttl %s)" % fmt_ttl(answers[0][0])
    if flags & 0x0200:
        text += " (truncated)"
    out("OK " + text)
    return 0


if __name__ == "__main__":
    sys.exit(main())



