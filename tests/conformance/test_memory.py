"""Memory is sized at start and stays flat (gate G6, issue #11): the resident size
after a long churn of connections, sessions, subscriptions, retained messages,
will messages and refusals is within a small margin of the size just after start.
The tables are written when they are allocated, so the margin is for the program's
own small allocations (log records) and the kernel's socket bookkeeping, not for
growth that follows the workload."""

import random
import re
import socket
import time
import unittest

from harness import Broker, Client, connect_packet, publish_packet, subscribe_packet, PINGREQ

MARGIN_KIB = 1024


def rss_kib(pid):
    with open("/proc/%d/status" % pid) as f:
        return int(re.search(r"VmRSS:\s+(\d+) kB", f.read()).group(1))


def thp():
    try:
        with open("/sys/kernel/mm/transparent_hugepage/enabled") as f:
            return f.read().strip()
    except OSError:
        return "unknown"


def largest_mappings(pid, n=4):
    """The n mappings with the most resident memory, for the failure message."""
    rows, cur = [], None
    with open("/proc/%d/smaps" % pid) as f:
        for line in f:
            m = re.match(r"^([0-9a-f]+)-([0-9a-f]+) \S+ \S+ \S+ \S+\s*(.*)$", line)
            if m:
                cur = [0, 0, int(m.group(2), 16) - int(m.group(1), 16), m.group(3) or "anon"]
                rows.append(cur)
            elif cur is not None and line.startswith("Rss:"):
                cur[0] = int(line.split()[1])
            elif cur is not None and line.startswith("AnonHugePages:"):
                cur[1] = int(line.split()[1])
    rows.sort(key=lambda r: -r[0])
    return ["rss %d KiB (huge %d KiB) of %d KiB: %s" % (r[0], r[1], r[2] // 1024, r[3]) for r in rows[:n]]


def burst(port, n=400):
    """Open `n` connections at once, subscribe, publish to all of them, close: the peak
    that later sequential churn cannot exceed, so the warm-up reaches the high-water mark
    of every pool (buffers are attached while in use, so memory follows the peak)."""
    clients = []
    try:
        for i in range(n):
            c = Client(port, timeout=10)
            c.connect("burst%d" % i, clean=bool(i % 2))
            c.subscribe([("burst/#", 1 + i % 2)])
            clients.append(c)
        for i in range(20):
            clients[0].publish("burst/x", b"b" * 200, qos=1)
        time.sleep(0.5)
    finally:
        for c in clients:
            c.close()
    time.sleep(0.5)


def churn(port, rng, rounds):
    for i in range(rounds):
        kind = rng.randrange(9)
        c = Client(port)
        try:
            name = "c%d" % rng.randrange(40)
            if kind == 0:
                c.connect(name, clean=True)
                c.subscribe([("t/%d/#" % rng.randrange(20), rng.randrange(2))])
                c.publish("t/%d/x" % rng.randrange(20), b"p" * rng.randrange(200), qos=rng.randrange(2),
                          retain=bool(rng.getrandbits(1)))
            elif kind == 1:
                c.connect(name, clean=False)
                c.subscribe([("p/%d" % rng.randrange(10), 1)])
            elif kind == 2:
                c.connect("w%d" % rng.randrange(10), will=("will/%d" % rng.randrange(5), b"w" * 30, 1, True))
                c.sock.close()
            elif kind == 3:
                c.send(b"\x30\x05\x00\x01t")      # not a CONNECT first
            elif kind == 4:
                c.connect(name, clean=rng.random() < 0.5)
                c.publish("p/%d" % rng.randrange(10), b"q" * 50, qos=1)
            elif kind == 5:
                c.send(connect_packet("big") + publish_packet("x/" + "y" * 300, b"z"))
            elif kind == 6:
                c.connect(name, clean=True)
                c.send(b"\xe0\x00")
            elif kind == 7:
                # QoS 2 both ways, left half done on purpose: the received set and the PUBREL
                # entries of a persistent session are state that must not grow.
                c.connect(name, clean=False)
                c.subscribe([("p/%d" % rng.randrange(10), 2)])
                c.send(publish_packet("p/%d" % rng.randrange(10), b"q" * 40, qos=2, pid=rng.randrange(1, 40)))
            else:
                c.connect(name, clean=True)
                c.publish2("t/%d/x" % rng.randrange(20), b"r" * 30, pid=rng.randrange(1, 40))
        except Exception:
            pass
        finally:
            c.close()


class Memory(unittest.TestCase):
    def test_resident_size_is_flat_under_churn(self):
        with Broker("--offline-sessions", "32", "--retained-messages", "64", "--subscriptions-total", "512",
                    "--max-nodes", "512") as broker:
            rng = random.Random(7)
            churn(broker.port, rng, 2500)      # warm up: first use of each path, and the allocator's first growth
            time.sleep(0.5)
            before = rss_kib(broker.proc.pid)
            samples = []
            for _ in range(8):
                churn(broker.port, rng, 750)
                time.sleep(0.3)
                samples.append(rss_kib(broker.proc.pid))
            self.assertTrue(broker.alive())
            self.assertLessEqual(max(samples) - before, MARGIN_KIB,
                                 "resident size grew from %d KiB: %r (transparent huge pages: %s)\n%s"
                                 % (before, samples, thp(), "\n".join(largest_mappings(broker.proc.pid))))
            # And it has stopped growing: the last windows are no larger than the first.
            self.assertLessEqual(max(samples[4:]) - max(samples[:4]), 256,
                                 "resident size is still growing: %r" % samples)
            # And it did not just stop answering.
            c = broker.client()
            c.connect("after")
            c.send(PINGREQ)
            c.expect(13)

    def test_an_idle_connection_costs_less_than_a_page(self):
        # A connection's input and queue buffers are attached only while something is in
        # them, so a connected, subscribed, idle client touches no page of its own. (They
        # were fixed per-connection slabs, 8.3 KiB each, until this was measured.)
        with Broker("--max-connections", "4096", "--stats-seconds", "0") as broker:
            clients = []

            def add(n):
                while len(clients) < n:
                    c = broker.client(timeout=10)
                    c.connect("idle%d" % len(clients), keepalive=300)
                    c.subscribe([("idle/%d" % len(clients), 0)])
                    clients.append(c)

            add(200)                              # warm-up: first use of each path
            time.sleep(0.3)
            before = rss_kib(broker.proc.pid)
            add(2200)
            time.sleep(0.5)
            per_connection = (rss_kib(broker.proc.pid) - before) / 2000.0
            self.assertLess(per_connection, 2.0, "%.2f KiB of resident size per idle connection" % per_connection)

    def test_split_packets_on_many_connections_at_once(self):
        # Each connection holds a spill slot while a packet is split across reads. Interleave
        # the halves on 300 connections so slots are taken, released and reused.
        with Broker("--max-connections", "512", "--stats-seconds", "0") as broker:
            clients = [broker.client() for _ in range(300)]
            for round_ in range(3):
                packets = [connect_packet("s%d" % i, clean=True) if round_ == 0 else
                           publish_packet("sp/%d" % i, b"x" * 700) for i, c in enumerate(clients)]
                for c, pk in zip(clients, packets):
                    c.send(pk[:3])
                time.sleep(0.2)
                for c, pk in zip(clients, packets):
                    c.send(pk[3:])
                if round_ == 0:
                    for c in clients:
                        c.expect(2)
            sub = broker.client()
            sub.connect("sp-sub")
            sub.subscribe("sp/#")
            clients[0].publish("sp/0", b"after")
            self.assertEqual(sub.recv_publish()[1], b"after")
            self.assertTrue(broker.alive())

    def test_resident_size_never_passes_the_estimate_the_log_states(self):
        # The tables are reserved at start and the operating system commits their pages as
        # they are used, so an idle broker is small and a full one is not larger than the
        # `memory_bytes` the log states plus the program itself.
        with Broker("--max-connections", "256", "--queue-bytes", "17000", "--max-packet", "4096",
                    "--offline-sessions", "16") as broker:
            estimate = [r for r in broker.lines if r["type"] == "listening"][0]["memory_bytes"]
            idle = rss_kib(broker.proc.pid) * 1024
            self.assertLess(idle, estimate)
            clients = []
            for i in range(256):
                c = broker.client()
                c.connect("full%d" % i)
                c.subscribe([("full/#", 1)])
                clients.append(c)
            pub = clients[0]
            for i in range(60):
                pub.publish("full/x", b"m" * 3000, qos=0)
            time.sleep(1.0)
            loaded = rss_kib(broker.proc.pid) * 1024
            self.assertGreater(loaded, idle)
            self.assertLessEqual(loaded, estimate + 8 * 1024 * 1024)
            for c in clients:
                c.close()


if __name__ == "__main__":
    unittest.main()
