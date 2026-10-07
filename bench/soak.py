#!/usr/bin/env python3
"""A soak: the broker under steady traffic and connection churn for a long time, with its resident size and open
descriptors sampled (docs/benchmark.md, "Soak").

    python3 bench/soak.py --seconds 1800 --out soak.csv

Three things run at once against one broker started with tight bounds, so that eviction, queue limits and retained limits are
exercised the whole time: (1) the churn of tests/conformance/test_memory.py (connects, subscriptions, wills, QoS 2 left half done,
malformed input), over and over; (2) a long-lived publisher and subscriber exchanging QoS 0 messages with a QoS 1 message every tenth
tick, the subscriber answering; (3) a sampler that writes a CSV line every 10 seconds (time, resident KiB, open descriptors, log
lines drained) and keeps the broker's log pipe drained, because a broker whose log is not read blocks (docs/design.md, Gap 8).

It prints, at the end, what happened and stops the broker with SIGTERM to read its `end` record. It does not decide whether the
numbers are acceptable: it reports them, and the document that quotes them says what they were measured on and for how long.
"""

import argparse
import os
import pathlib
import random
import statistics
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tests" / "conformance"))

from harness import Broker, Client, publish_packet, puback_packet, subscribe_packet  # noqa: E402
import test_memory as tm  # noqa: E402

STOP = threading.Event()
STATS = {"churn_rounds": 0, "published": 0, "received": 0, "errors": []}


def churner(port):
    rng = random.Random(11)
    while not STOP.is_set():
        tm.churn(port, rng, 100)
        STATS["churn_rounds"] += 100


def traffic(port):
    """A long-lived subscriber and publisher; the connections are never closed while the soak runs."""
    try:
        sub = Client(port, timeout=5)
        sub.connect("soak-sub", keepalive=0)
        sub.subscribe([("soak/#", 1)])
        pub = Client(port, timeout=5)
        pub.connect("soak-pub", keepalive=0)
    except Exception as e:
        STATS["errors"].append("setup: %r" % e)
        return

    def reader():
        while not STOP.is_set():
            try:
                k, flags, body = sub.recv(1.0)
            except TimeoutError:
                continue
            except Exception as e:
                STATS["errors"].append("subscriber: %r" % e)
                return
            if k == 3:
                STATS["received"] += 1
                if (flags >> 1) & 3 == 1:
                    tl = int.from_bytes(body[:2], "big")
                    sub.send(puback_packet(int.from_bytes(body[2 + tl:4 + tl], "big")))

    threading.Thread(target=reader, daemon=True).start()
    tick = 0
    while not STOP.is_set():
        try:
            for i in range(50):
                pub.send(publish_packet("soak/q0", b"x" * 100, qos=0))
                STATS["published"] += 1
            if tick % 10 == 0:
                pub.send(publish_packet("soak/q1", b"y" * 100, qos=1, pid=1 + tick % 60000))
                k, _, _ = pub.recv(5.0)
                while k != 4:
                    k, _, _ = pub.recv(5.0)
                STATS["published"] += 1
        except Exception as e:
            STATS["errors"].append("publisher: %r" % e)
            return
        tick += 1
        time.sleep(0.1)


def fds(pid):
    try:
        return len(os.listdir("/proc/%d/fd" % pid))
    except OSError:
        return -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=1800)
    ap.add_argument("--out", default="soak.csv")
    ap.add_argument("--warmup", type=int, default=120, help="seconds before the baseline resident size is taken")
    a = ap.parse_args()
    broker = Broker("--offline-sessions", "32", "--retained-messages", "64", "--subscriptions-total", "512",
                    "--max-nodes", "512", "--stats-seconds", "0", no_thp=True)
    threading.Thread(target=churner, args=(broker.port,), daemon=True).start()
    threading.Thread(target=traffic, args=(broker.port,), daemon=True).start()
    rows = []
    start = time.time()
    lines = 0
    with open(a.out, "w") as out:
        out.write("seconds,rss_kib,fds,log_lines,churn_rounds,published,received\n")
        while time.time() - start < a.seconds and broker.alive() and not STATS["errors"]:
            time.sleep(10)
            broker.drain()
            lines += len(broker.lines)
            del broker.lines[:]
            t = time.time() - start
            row = (round(t), tm.rss_kib(broker.proc.pid), fds(broker.proc.pid), lines, STATS["churn_rounds"], STATS["published"], STATS["received"])
            rows.append(row)
            out.write(",".join(map(str, row)) + "\n")
            out.flush()
    STOP.set()
    time.sleep(1)
    alive = broker.alive()
    status, records = broker.stop()
    end = [r for r in records if r.get("type") == "end"]
    print("ran %d s; broker alive at the end: %s; exit status %s; end record: %s" % (round(time.time() - start), alive, status, end[-1] if end else None))
    print("churn rounds %d, published %d, received %d, errors %r" % (STATS["churn_rounds"], STATS["published"], STATS["received"], STATS["errors"][:3]))
    base = [r for r in rows if r[0] >= a.warmup]
    if len(base) >= 4:
        half = len(base) // 2
        first, second = base[:half], base[half:]
        t = [r[0] for r in second]
        y = [r[1] for r in second]
        slope = statistics.linear_regression(t, y).slope * 3600 if len(second) > 2 else 0.0
        print("resident KiB after warm-up: first %d, max of first half %d, max of second half %d, last %d; slope over the second half %.0f KiB/hour"
              % (base[0][1], max(r[1] for r in first), max(r[1] for r in second), base[-1][1], slope))
        print("open descriptors: first %d, last %d, max %d" % (base[0][2], base[-1][2], max(r[2] for r in base)))
    broker.__exit__(None, None, None)


if __name__ == "__main__":
    main()
