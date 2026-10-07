"""Hardening (gate G7, issue #11): no input reaches a trap. Random byte streams,
mutated valid packets, huge declared lengths, packets split at random points and
abrupt resets are thrown at a broker; afterwards it must be alive, serve a normal
client, hold no connection it should have closed, and have logged only rules in
the catalogue. The seeds are fixed, so a failure reproduces."""

import random
import socket
import struct
import time
import unittest

from harness import (Broker, Client, Closed, PINGREQ, connect_packet, publish_packet, subscribe_packet,
                     unsubscribe_packet, puback_packet, pubrec_packet, pubrel_packet, pubcomp_packet, pkt, s16, run, records)

SEEDS = [1, 2, 3, 4, 5, 6, 7, 8]
CASES = 400


def valid_stream(rng):
    """A plausible session: CONNECT, then a few packets of every kind."""
    out = connect_packet("fz%d" % rng.randrange(8), clean=bool(rng.getrandbits(1)),
                         keepalive=rng.choice([0, 1, 60]),
                         will=rng.choice([None, ("w/t", b"gone", rng.randrange(2), bool(rng.getrandbits(1)))]),
                         username=rng.choice([None, "u"]), password=rng.choice([None, "p"]) if False else None)
    for _ in range(rng.randrange(1, 6)):
        kind = rng.randrange(7)
        topic = rng.choice(["a", "a/b", "a/b/c", "/", "$x/y", "a//b"])
        if kind == 0:
            out += publish_packet(topic, bytes(rng.randrange(256) for _ in range(rng.randrange(40))),
                                  qos=rng.randrange(3), retain=bool(rng.getrandbits(1)), pid=rng.randrange(1, 100))
        elif kind == 1:
            out += subscribe_packet([(rng.choice(["a/#", "a/+", "#", "+/+", "a/b"]), rng.randrange(3))
                                     for _ in range(rng.randrange(1, 4))], pid=rng.randrange(1, 100))
        elif kind == 2:
            out += unsubscribe_packet([rng.choice(["a/#", "a/b"])], pid=rng.randrange(1, 100))
        elif kind == 3:
            out += rng.choice([puback_packet, pubrec_packet, pubrel_packet, pubcomp_packet])(rng.randrange(0, 100))
        elif kind == 4:
            out += PINGREQ
        elif kind == 5:
            out += pkt(rng.randrange(256), bytes(rng.randrange(256) for _ in range(rng.randrange(8))))
        else:
            out += b"\xe0\x00"
    return out


def mutate(rng, data):
    data = bytearray(data)
    for _ in range(rng.randrange(1, 6)):
        if not data:
            break
        how = rng.randrange(5)
        i = rng.randrange(len(data))
        if how == 0:
            data[i] = rng.randrange(256)
        elif how == 1:
            data[i] ^= 1 << rng.randrange(8)
        elif how == 2:
            del data[i:i + rng.randrange(1, 5)]
        elif how == 3:
            data[i:i] = bytes(rng.randrange(256) for _ in range(rng.randrange(1, 5)))
        else:
            data = data[:i]
    return bytes(data)


def hostile(rng):
    kind = rng.randrange(6)
    if kind == 0:
        return bytes(rng.randrange(256) for _ in range(rng.randrange(1, 300)))
    if kind == 1:
        return mutate(rng, valid_stream(rng))
    if kind == 2:
        # a declared length of up to 256 MiB, then a little data
        return bytes([rng.choice([0x10, 0x30, 0x82, 0xa2])]) + bytes([0xff, 0xff, 0xff, 0x7f]) + b"x" * rng.randrange(20)
    if kind == 3:
        return connect_packet("big") + bytes([0x30, 0xff, 0xff, 0x03]) + b"y" * rng.randrange(1000)
    if kind == 4:
        return valid_stream(rng) + mutate(rng, valid_stream(rng))
    return connect_packet("ok") + mutate(rng, publish_packet("a/b", b"z" * rng.randrange(30), qos=1, pid=7))


def throw(port, rng):
    data = hostile(rng)
    s = socket.create_connection(("127.0.0.1", port), timeout=2)
    try:
        s.setblocking(False)
        i = 0
        while i < len(data):
            n = rng.choice([1, 2, 3, 7, 50, len(data)])
            try:
                s.send(data[i:i + n])
            except (BlockingIOError, InterruptedError):
                time.sleep(0.001)
                continue
            i += n
            try:
                s.recv(65536)
            except (BlockingIOError, InterruptedError):
                pass
        if rng.getrandbits(1):
            # an abrupt reset rather than a close
            s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    except OSError:
        pass
    finally:
        s.close()


class Fuzz(unittest.TestCase):
    def survive(self, flags, seed):
        rng = random.Random(seed)
        with Broker(*flags) as broker:
            for _ in range(CASES):
                throw(broker.port, rng)
            time.sleep(0.6)
            self.assertTrue(broker.alive(), "the broker died on seed %d" % seed)
            # It still serves a normal client.
            c = broker.client()
            c.connect("after-fuzz")
            c.subscribe("probe")
            c.publish("probe", b"alive")
            self.assertEqual(c.recv_publish()[:2], ("probe", b"alive"))
            c.close()
            time.sleep(0.4)
            status, lines = broker.stop()
        self.assertEqual(status, 0)
        stats = [r for r in lines if r["type"] == "stats"][-1]
        self.assertEqual(stats["connections"], 0, "connections leaked on seed %d" % seed)
        self.assertEqual(stats["sessions_online"], 0)
        known = set(rec["tag"] for rec in records(run("rules")[1]) if rec["type"] == "rule")
        for r in lines:
            if r["type"] == "refusal":
                self.assertIn(r["rule"], known)
        self.assertEqual(lines[-1]["type"], "end")
        return stats

    def test_default_bounds(self):
        for seed in SEEDS:
            self.survive([], seed)

    def test_tight_bounds(self):
        # every bound small, so the limits are the commonest outcome
        flags = ["--max-connections", "16", "--max-packet", "256", "--queue-bytes", "1024", "--queue-messages", "4",
                 "--subscriptions-per-client", "2", "--subscriptions-total", "8", "--max-nodes", "32",
                 "--retained-messages", "2", "--retained-slot-bytes", "64", "--offline-sessions", "2",
                 "--topic-max", "16", "--topic-levels", "3", "--will-bytes", "32", "--client-id-max", "24",
                 "--connect-timeout", "1", "--write-stall", "1"]
        for seed in SEEDS:
            self.survive(flags, 100 + seed)

    def test_slowloris_connections_are_dropped(self):
        with Broker("--connect-timeout", "1", "--max-connections", "64") as broker:
            idle = []
            for _ in range(40):
                s = socket.create_connection(("127.0.0.1", broker.port))
                s.sendall(b"\x10")
                idle.append(s)
            time.sleep(2.6)
            self.assertTrue(broker.alive())
            stats_seen = broker.events("refusal", "timeout.connect", wait=2)
            self.assertGreaterEqual(len(stats_seen), 1)
            c = broker.client()
            c.connect("fits-again")
            for s in idle:
                s.close()
            status, lines = broker.stop()
        self.assertEqual([r for r in lines if r["type"] == "stats"][-1]["refusals"]["timeout.connect"], 40)


if __name__ == "__main__":
    unittest.main()
