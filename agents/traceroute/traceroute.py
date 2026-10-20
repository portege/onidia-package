#!/usr/bin/env python3
"""traceroute - the hop-by-hop network path to a host, one page per hop.

Reads one "RUN {json}" line on stdin, walks IPv4 TTLs toward the target,
answers "OK <message>". Pure stdlib, no API key, no root, no external
traceroute binary, no dependencies.

WHY NO RAW SOCKETS AND NO ROOT
------------------------------
Classic traceroute needs a raw ICMP socket to hear "time exceeded", which
means root. This uses the Linux error queue instead: IP_RECVERR on an
ordinary connected UDP socket makes the kernel queue every ICMP answer to
our probes, and recvmsg(MSG_ERRQUEUE) reads them back unprivileged - the
mechanism tracepath(8) uses. CPython does not export the IP_RECVERR
constant, so the linux/in.h value (11) is used directly. Linux only, which
is the only platform onidia runs on; anything else fails loudly at startup
rather than hanging.

WHY ONE PROBE PER HOP
---------------------
traceroute sends three probes per TTL, tracepath sends one. One keeps a
20-hop run inside about 25 seconds of worst case (each silent hop costs
exactly one 1.2s wait) so the run never trips the brain's 60s ability
budget, and every RTT shown is a real measurement, not "2 of 3 lost"
noise from a router that filters half its ICMP.

WHY ONE HOP PER PAGE, NOT ONE LINE
----------------------------------
The reader in agent/external.go stops scanning at the first OK line, so
newlines inside the message are silently lost (agents/README.md rule 2).
A raw form-feed byte is different: it is not a line break, the scanner
passes it through inside the OK line, and both the chat window
(ui.go splitPages) and the buddy bubble (onidia/bubble_page.go paginate)
render it as an explicit page break with prev/next controls. A trace is a
LIST of hops - 15+ entries at ~17 characters each - and one hop per page
with < / > paging is the only layout that stays readable in a 22-column
bubble. The hold is extended per page (onidia/speech.go holdDuration), so
the trace does not fade away mid-read.

WHY PLAIN ASCII
---------------
The 5x9 bitmap font draws printable ASCII only; reverse-DNS names and IPs
are ASCII anyway, and fold() (copied from weather/ per agents/README.md)
catches the odd label that is not.
"""
import html
import json
import random
import re
import socket
import struct
import sys
import time
import unicodedata

# Not exported by CPython; the value from linux/in.h, stable since 2.2.
IP_RECVERR = 11

# Per-hop wait. Short enough that a filtered hop (there is always one) does
# not stall the run, long enough that a real cross-ocean hop answers.
WAIT = 1.2
RDNS_WAIT = 0.9            # per name, best effort - the trace is the payload
DEFAULT_HOPS = 20
MAX_HOPS_CAP = 40
PORT = 33434               # the classic traceroute target port
NAME_W = 16                # hop label column; keeps lines inside ~22 chars

# errno values read off the error queue, from asm-generic/errno.h:
ERR_ECONNREFUSED = 111     # the target closed the port: we ARRIVED

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
    """Fit a label into its column."""
    if len(text) <= width:
        return text
    return text[:width - 3] + "..."


def resolvers():
    """Server IPs for the reverse lookups, from /etc/resolv.conf."""
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


def reverse_ptr(ip):
    """The in-addr.arpa name whose PTR record is `ip`'s hostname."""
    return ".".join(reversed(ip.split("."))) + ".in-addr.arpa"


def encode_name(name):
    """Wire-format QNAME (RFC 1035)."""
    wire = b""
    for label in name.rstrip(".").split("."):
        if not label or len(label) > 63:
            raise ValueError("bad name %r" % name)
        wire += bytes([len(label)]) + label.encode("ascii", "replace")
    return wire + b"\x00"


def decode_name(data, off):
    """(name, next_off) with compression pointers, loop-guarded."""
    labels = []
    end = None
    seen = set()
    while off < len(data):
        if off in seen:
            raise ValueError("compression loop")
        seen.add(off)
        n = data[off]
        if n == 0:
            off += 1
            return ".".join(labels), (end if end is not None else off)
        if n & 0xC0 == 0xC0:
            if off + 1 >= len(data):
                raise ValueError("truncated pointer")
            if end is None:
                end = off + 2
            off = ((n & 0x3F) << 8) | data[off + 1]
        elif n & 0xC0:
            raise ValueError("reserved label bits")
        else:
            labels.append(data[off + 1:off + 1 + n].decode("ascii", "replace"))
            off += 1 + n
    raise ValueError("truncated name")


def lookup_ptr(ip, servers):
    """Hostname for a hop, or None. Best effort: a router that does not
    publish rDNS gets no name, and a slow server costs one short wait.

    Kept as a local mini-client (same wire codec as agents/dns/) so one
    folder stays one self-contained install - agents cannot import each
    other, by design of the install model.
    """
    tid = random.randint(0, 0xFFFF)
    pkt = struct.pack(">HHHHHH", tid, 0x0100, 1, 0, 0, 0)
    pkt += encode_name(reverse_ptr(ip)) + struct.pack(">HH", 12, 1)  # PTR
    for server in servers[:2]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(RDNS_WAIT)
            sock.sendto(pkt, (server, 53))
            data, _ = sock.recvfrom(2048)
        except (OSError, ValueError):
            continue
        finally:
            sock.close()
        try:
            if len(data) < 12 or struct.unpack_from(">H", data)[0] != tid:
                continue
            an = struct.unpack_from(">H", data, 6)[0]
            off = 12
            _, off = decode_name(data, off)      # skip the question
            off += 4
            for _ in range(an):
                _, off = decode_name(data, off)
                rtype, _, _, rlen = struct.unpack_from(">HHIH", data, off)
                off += 10
                if rtype == 12:
                    name, _ = decode_name(data, off)
                    if name:
                        return name.rstrip(".")
                off += rlen
        except (ValueError, struct.error):
            continue
    return None


def resolve(host):
    """First IPv4 address for host, or raise the resolver's gaierror."""
    infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_DGRAM)
    return infos[0][4][0]


def drain(s):
    """Empty stale errors so a late reply cannot be misread as the NEXT
    hop's answer. One probe is in flight at a time; anything queued here
    belongs to a probe we already scored."""
    while True:
        try:
            s.recvmsg(0, 256, socket.MSG_ERRQUEUE | socket.MSG_DONTWAIT)
        except (BlockingIOError, OSError):
            return


def read_err(s):
    """(ip, errno) from one queued ICMP error, or (None, None).

    Layout on the errqueue: struct sock_extended_err (16 bytes, native
    endian) followed by the sockaddr of the node that answered. errno
    ECONNREFUSED means the target itself rejected our port - that is the
    arrival signal, the UDP twin of traceroute's final hop.
    """
    _, ancdata, _, _ = s.recvmsg(64, 256, socket.MSG_ERRQUEUE)
    for level, ctype, data in ancdata:
        if level == socket.SOL_IP and ctype == IP_RECVERR and len(data) >= 16:
            errno = struct.unpack_from("=I", data, 0)[0]
            if len(data) >= 24 and struct.unpack_from("=H", data, 16)[0] == 2:
                return socket.inet_ntoa(data[20:24]), errno
    return None, None


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

    host = str(args.get("host", "")).strip()
    if not host:
        out("ERR host is required")
        return 1
    if "://" in host:      # a URL pasted by the model: keep the host part
        host = host.split("://", 1)[1].split("/", 1)[0]
    host = host.rstrip(".")

    if not sys.platform.startswith("linux"):
        out("ERR traceroute needs Linux (kernel error queue), this is %s"
            % sys.platform)
        return 1

    try:
        hops_max = int(str(args.get("max_hops", DEFAULT_HOPS)).strip()
                       or DEFAULT_HOPS)
    except ValueError:
        out("ERR max_hops must be a number, 1-%d" % MAX_HOPS_CAP)
        return 1
    hops_max = max(1, min(MAX_HOPS_CAP, hops_max))

    try:
        dst = resolve(host)
    except socket.gaierror:
        out("ERR cannot resolve %r" % host)
        return 1

    out("INFO traceroute to %s (%s), max %d hops" % (fold(host), dst, hops_max))
    # "expr", NOT "expression": that is pet_control's INPUT vocabulary, and a
    # malformed PET directive fails the whole run (see agents/README.md).
    out("PET expr thinking")   # she looks like she is tracing

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # BEFORE connect, as tracepath does: connect() consults the flag.
        sock.setsockopt(socket.SOL_IP, IP_RECVERR, 1)
    except (OSError, ValueError) as e:
        sock.close()
        out("ERR kernel rejected IP_RECVERR: %s" % e)
        return 1
    sock.settimeout(WAIT)
    try:
        sock.connect((dst, PORT))
    except OSError as e:
        sock.close()
        out("ERR no route to %s: %s" % (dst, e))
        return 1

    # The trace itself: one probe per TTL, scored off the error queue.
    hops = []          # (ttl, ip or None, rtt_ms or None)
    reached = False
    loops = False
    try:
        for ttl in range(1, hops_max + 1):
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_TTL, ttl)
            except OSError as e:
                out("ERR cannot set TTL: %s" % e)
                return 1
            drain(sock)          # late answers from the previous TTL
            t0 = time.time()
            try:
                sock.send(b"onidia" + bytes([ttl & 0xFF]))
            except OSError as e:
                out("ERR send failed at hop %d: %s" % (ttl, e))
                return 1
            try:
                ip, errno = read_err(sock)
            except (socket.timeout, TimeoutError):
                hops.append((ttl, None, None))
                continue
            except OSError as e:
                out("ERR reading replies: %s" % e)
                return 1
            rtt = (time.time() - t0) * 1000.0
            hops.append((ttl, ip, rtt))
            if errno == ERR_ECONNREFUSED:
                reached = True     # the target answered: port unreachable
                break
            if ip == dst:
                reached = True     # some stacks answer on the socket itself
                break
            # The same router answering three TTLs in a row is a loop (or
            # asymmetric mud): stop rather than burn the rest of the budget.
            tail = [h[1] for h in hops[-3:] if h[1]]
            if len(tail) == 3 and len(set(tail)) == 1:
                loops = True
                break
    finally:
        sock.close()

    answered = [h for h in hops if h[1]]
    if not answered:
        out("ERR no reply from %s (%s) in %d hops - packets are filtered or "
            "the network is down" % (fold(host), dst, len(hops)))
        return 1

    # Reverse DNS as a second pass: the trace is already scored, so a slow
    # rDNS server delays the labels, never the route discovery.
    servers = resolvers()
    names = {}
    for _, ip, _ in answered:
        if ip not in names:
            names[ip] = lookup_ptr(ip, servers)

    # ONE OK line - the reader stops at its end. The \f bytes inside it are
    # page breaks, not newlines: see the module docstring.
    pages = ["trace %s (%s)" % (fold(host), dst)]
    for ttl, ip, rtt in hops:
        if ip is None:
            pages.append("%d * no reply" % ttl)
            continue
        name = names.get(ip)
        label = clip(fold(name), NAME_W) if name else clip(ip, NAME_W)
        pages.append("%d %s %dms" % (ttl, label, round(rtt)))
    if reached:
        pages.append("%s reached in %d hops (%dms)"
                     % (fold(host), hops[-1][0], round(hops[-1][2])))
    elif loops:
        pages.append("route loops from hop %d - stopped" % hops[-1][0])
    else:
        pages.append("no reply from %s after %d hops" % (fold(host), hops[-1][0]))
    out("OK " + "\f".join(pages))
    return 0


if __name__ == "__main__":
    sys.exit(main())





