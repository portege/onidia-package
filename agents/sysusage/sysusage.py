#!/usr/bin/env python3
"""sysusage - report CPU, memory, load average and disk I/O (protocol agent-line-v1).

Reads one "RUN {json}" line on stdin, samples /proc twice, answers
"OK <message>". Pure stdlib, no dependencies.

WHY PLAIN ASCII AND NO UNICODE BOX DRAWING
-----------------------------------------
The reply is drawn with a 5x9 bitmap font that has no glyph for U+2500-style
box characters: an unknown rune advances and draws nothing, so a pretty
markdown table would come out as blank gaps. Every printable ASCII rune has a
glyph (backtick excepted) and the font is fixed-pitch, so "#" and "-" columns
line up exactly.

WHY EACH LINE IS A PAGE
-----------------------
onidia-chat turns every newline in a reply into a form-feed page break, so a
multi-line answer becomes a paged bubble with prev/next controls. That is used
deliberately here: one metric per page keeps each bar meter intact instead of
being word-wrapped into a smear. `brief = true` emits a single line.

A chat bubble is only ~25 characters wide at the default 456px window
(3/4 of the window, 12px per glyph), so the lines are sized to fit that.
Launch with `-w 900` for roomier meters.
"""
import json
import os
import sys
import time

# Sample window. Long enough that a burst shows up, short enough that asking
# "how busy is it?" feels instant.
SAMPLE_S = 0.25

# Only whole disks, not their partitions: sd1 is a slice of the same device,
# and counting both would double the number.
WHOLE_DISK = ("sd", "nvme", "mmcblk", "vd", "hd")

BAR_W = 10


def out(line):
    print(line, flush=True)  # the brain reads line-by-line: never buffer


def cpu_times():
    """(total jiffies, idle jiffies) from /proc/stat's aggregate cpu line."""
    with open("/proc/stat") as f:
        fields = f.readline().split()
    vals = [int(v) for v in fields[1:]]
    # user nice system idle iowait irq softirq steal guest guest_nice
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return sum(vals), idle


def cpu_busy_pct(t0, t1):
    """Busy fraction between two samples. iowait counts as idle: a machine
    blocked on disk is not doing work, and folding it in would report a
    saturated CPU when the real problem is storage."""
    total = t1[0] - t0[0]
    idle = t1[1] - t0[1]
    if total <= 0:
        return None
    return 100.0 * (total - idle) / total


def memory():
    """(used_pct, used_MiB, total_MiB) using MemAvailable, which accounts for
    reclaimable page cache. MemFree alone would report a busy machine as
    nearly empty the moment it touched a file."""
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts:
                info[key] = int(parts[0])  # kB
    total = info.get("MemTotal", 0)
    if total <= 0:
        return None
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    used = total - avail
    return (100.0 * used / total, used / 1024.0, total / 1024.0)


def load():
    """(1min, 5min, 15min, cores). Load is not a percentage - it is a count of
    runnable threads - so it is reported as-is, with the core count for scale."""
    one, five, fifteen = os.getloadavg()
    return one, five, fifteen, (os.cpu_count() or 1)


def disk_counters():
    """(read_sectors, write_sectors) summed over whole disks."""
    r = w = 0
    try:
        with open("/proc/diskstats") as f:
            for line in f:
                p = line.split()
                if len(p) < 11:
                    continue
                if not p[2].startswith(WHOLE_DISK):
                    continue
                r += int(p[5])   # sectors read
                w += int(p[9])   # sectors written
    except OSError:
        return None
    return r, w


def rate_mib(now, before, seconds):
    """MiB/s between two counter samples. 512-byte sectors."""
    if now is None or before is None:
        return None, None
    return ((now[0] - before[0]) * 512.0 / seconds / (1 << 20),
            (now[1] - before[1]) * 512.0 / seconds / (1 << 20))


def bar(frac, width=BAR_W):
    """ASCII meter. '#' filled, '-' empty. Fixed pitch, so it lines up."""
    if frac is None:
        return "[" + "?" * width + "]"
    frac = 0.0 if frac < 0 else (1.0 if frac > 1 else frac)
    filled = int(round(frac * width))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def rate(v):
    """Throughput as a short ASCII token: 1.2M / 340K / 0."""
    if v is None:
        return "-"
    if v >= 1.0:
        return "%.1fM" % v
    if v < 0.01:
        return "0"
    return "%.0fK" % (v * 1024.0)


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

    want = str(args.get("section", "all")).strip().lower() or "all"
    if want not in ("all", "cpu", "memory", "load", "io"):
        out("ERR section must be one of: all, cpu, memory, load, io")
        return 1
    brief = str(args.get("brief", "false")).strip().lower() in ("1", "true", "yes", "on")

    try:
        t0 = cpu_times()
        d0 = disk_counters()
        time.sleep(SAMPLE_S)
        t1 = cpu_times()
        d1 = disk_counters()
    except OSError as e:
        out("ERR cannot read /proc: %s" % e)
        return 1

    cpu = cpu_busy_pct(t0, t1)
    mem = memory()
    ld = load()
    r, w = rate_mib(d1, d0, SAMPLE_S)

    out("INFO sampled over %.2fs" % SAMPLE_S)
    # "expr", NOT "expression": that is pet_control's INPUT vocabulary. The PET
    # wire format only accepts action | event | expr | a movement verb, and a
    # malformed directive fails the whole run rather than being dropped - so
    # "PET expression thinking" turns the whole report into an error.
    out("PET expr thinking")   # she looks like she is checking

    if brief:
        # No newline, so this is always exactly ONE page - the user never has to
        # click through it. It is ~35 chars, which wraps to two visual lines in
        # the 25-char bubble of a default-width window; word wrapping stays
        # inside the page, unlike a newline.
        parts = []
        if want in ("all", "cpu") and cpu is not None:
            parts.append("CPU %d%%" % round(cpu))
        if want in ("all", "memory") and mem:
            parts.append("MEM %d%%" % round(mem[0]))
        if want in ("all", "load"):
            parts.append("LOAD %.2f" % ld[0])
        if want in ("all", "io"):
            parts.append("IO %s/%s" % (rate(r), rate(w)))
        if not parts:
            out("ERR nothing to report")
            return 1
        out("OK " + " ".join(parts))
        return 0

    lines = []
    if want in ("all", "cpu") and cpu is not None:
        lines.append("CPU %s %3d%%" % (bar(cpu / 100.0), round(cpu)))
    if want in ("all", "memory") and mem:
        lines.append("MEM %s %3d%%" % (bar(mem[0] / 100.0), round(mem[0])))
    if want in ("all", "load"):
        lines.append("LOAD %.2f %.2f %.2f" % ld[:3])
    if want in ("all", "io"):
        lines.append("DISK R %s W %s" % (rate(r), rate(w)))
    if not lines:
        out("ERR nothing to report")
        return 1

    out("OK " + "\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
