"""One fixture per connection-level rule (docs/design.md section 5, gate G8): the
test triggers the rule, checks what the client sees, and checks that the broker
logs a `refusal` with the rule's tag and counts it in its final `stats` record.
`test_every_rule_has_a_fixture` checks the fixtures are exactly the rules
`mqtt rules` lists, so a rule cannot be added or dropped without a test."""

import socket
import time
import unittest

from harness import (Broker, Client, Closed, PINGREQ, connect_packet, publish_packet, subscribe_packet,
                     pkt, s16, puback_packet, records, run, user_line)

FIXTURES = {}


def fixture(tag):
    def wrap(fn):
        FIXTURES[tag] = fn.__name__
        return fn
    return wrap


class Rules(unittest.TestCase):
    def start(self, *flags, users=None):
        broker = Broker(*flags, users=users)
        self.addCleanup(broker.__exit__, None, None, None)
        self.broker = broker
        return broker

    def client(self, broker=None):
        c = (broker or self.broker).client()
        self.addCleanup(c.close)
        return c

    def logged(self, tag, at_least=1):
        """The rule was logged, and the final counters count it."""
        found = self.broker.events("refusal", tag, wait=3.0)
        self.assertGreaterEqual(len(found), 1, "no refusal record for %s" % tag)
        status, lines = self.broker.stop()
        self.assertEqual(status, 0)
        stats = [r for r in lines if r["type"] == "stats"][-1]
        self.assertGreaterEqual(stats["refusals"][tag], at_least, "counter for %s" % tag)
        self.assertEqual(lines[-1]["type"], "end")
        return found

    # ---- connection ------------------------------------------------------

    @fixture("protocol.connect-first")
    def test_connect_first(self):
        self.start()
        c = self.client()
        c.send(PINGREQ)
        self.assertTrue(c.closed())
        self.logged("protocol.connect-first")

    @fixture("protocol.connect-twice")
    def test_connect_twice(self):
        self.start()
        c = self.client()
        c.connect("a")
        c.send(connect_packet("a"))
        self.assertTrue(c.closed())
        found = self.logged("protocol.connect-twice")
        self.assertEqual(found[0]["client_id"], "a")

    @fixture("timeout.connect")
    def test_connect_timeout(self):
        self.start("--connect-timeout", "1")
        c = self.client()
        start = time.time()
        self.assertTrue(c.closed(timeout=6.0))
        self.assertLess(time.time() - start, 5.0)
        self.logged("timeout.connect")

    @fixture("protocol.bad-name")
    def test_bad_protocol_name(self):
        self.start()
        c = self.client()
        c.send(connect_packet("a", name=b"MQTX"))
        self.assertTrue(c.closed())
        self.logged("protocol.bad-name")

    @fixture("protocol.unsupported-level")
    def test_unsupported_level(self):
        self.start()
        c = self.client()
        c.connect("a", level=5, expect_code=1)
        self.assertTrue(c.closed())
        self.logged("protocol.unsupported-level")

    @fixture("protocol.client-id-rejected")
    def test_client_id_rejected(self):
        self.start("--client-id-max", "30")
        c = self.client()
        c.connect("", clean=False, expect_code=2)
        self.assertTrue(c.closed())
        d = self.client()
        d.connect("x" * 31, expect_code=2)
        self.assertTrue(d.closed())
        e = self.client()
        e.send(connect_packet("bad\x00id"))
        self.assertTrue(e.closed())
        self.logged("protocol.client-id-rejected", 3)

    @fixture("protocol.reserved-flags")
    def test_reserved_flags(self):
        self.start()
        c = self.client()
        c.send(connect_packet("a", flags_extra=1))
        self.assertTrue(c.closed())
        d = self.client()
        d.connect("b")
        d.send(pkt(0x80, b"\x00\x01" + s16("t") + b"\x00"))
        self.assertTrue(d.closed())
        self.logged("protocol.reserved-flags", 2)

    @fixture("protocol.remaining-length")
    def test_remaining_length(self):
        self.start()
        c = self.client()
        c.connect("a")
        c.send(b"\x30\xff\xff\xff\xff\x01")
        self.assertTrue(c.closed())
        self.logged("protocol.remaining-length")

    @fixture("limit.packet-size")
    def test_packet_size(self):
        self.start("--max-packet", "128", "--queue-bytes", "1024")
        c = self.client()
        c.connect("a")
        c.send(publish_packet("t", b"x" * 200))
        self.assertTrue(c.closed())
        self.logged("limit.packet-size")

    @fixture("protocol.malformed-packet")
    def test_malformed_packet(self):
        self.start()
        c = self.client()
        c.send(pkt(0x10, b"\x00\x04MQTT\x04"))
        self.assertTrue(c.closed())
        self.logged("protocol.malformed-packet")

    @fixture("protocol.topic-invalid")
    def test_topic_invalid(self):
        self.start()
        c = self.client()
        c.connect("a")
        c.send(publish_packet("a/#", b"x"))
        self.assertTrue(c.closed())
        self.logged("protocol.topic-invalid")

    @fixture("protocol.filter-invalid")
    def test_filter_invalid(self):
        self.start()
        c = self.client()
        c.connect("a")
        self.assertEqual(c.subscribe([("a/#/b", 0), ("a+", 0), ("ok", 0)]), [0x80, 0x80, 0])
        self.logged("protocol.filter-invalid", 2)

    @fixture("protocol.packet-id")
    def test_packet_id(self):
        self.start()
        c = self.client()
        c.connect("a")
        c.send(publish_packet("t", b"x", qos=1, pid=0))
        self.assertTrue(c.closed())
        self.logged("protocol.packet-id")

    @fixture("limit.qos2-inbound")
    def test_qos2_inbound(self):
        # Two QoS 2 messages are held for their PUBREL; a third, with another identifier, is
        # refused and not routed (design section 7a).
        self.start("--qos2-inbound", "2")
        sub = self.client()
        sub.connect("s")
        sub.subscribe([("t", 2)])
        c = self.client()
        c.connect("a")
        for pid in (1, 2):
            c.send(publish_packet("t", b"m%d" % pid, qos=2, pid=pid))
            c.expect(5)
        c.send(publish_packet("t", b"m3", qos=2, pid=3))
        self.assertTrue(c.closed())
        got = [sub.recv_publish()[1] for _ in range(2)]
        self.assertEqual(got, [b"m1", b"m2"])
        with self.assertRaises(TimeoutError):
            sub.recv_publish(timeout=0.5)
        self.logged("limit.qos2-inbound")

    @fixture("protocol.reserved-topic")
    def test_reserved_topic(self):
        # A PUBLISH under `$SYS/` is acknowledged as usual and goes nowhere (design section 7b).
        self.start()
        c = self.client()
        c.connect("a")
        c.send(publish_packet("$SYS/broker/uptime", b"forged", qos=1, pid=3))
        c.expect(4)
        self.logged("protocol.reserved-topic")

    @fixture("auth.required")
    def test_auth_required(self):
        # With a credential table, a CONNECT without a user name is not authorised (CONNACK 5).
        self.start(users=user_line("alice", "pw"))
        c = self.client()
        c.connect("a", expect_code=5)
        self.assertTrue(c.closed())
        self.logged("auth.required")

    @fixture("auth.bad-credentials")
    def test_auth_bad_credentials(self):
        self.start(users=user_line("alice", "pw"))
        c = self.client()
        c.connect("a", expect_code=4, username="alice", password="wrong")
        self.assertTrue(c.closed())
        self.logged("auth.bad-credentials")

    @fixture("limit.auth-budget")
    def test_auth_budget(self):
        # Two logins of 1,000 iterations do not fit in a budget of 1,500 in one second; the second is refused (CONNACK 3).
        self.start("--auth-budget", "1500", users=user_line("alice", "pw", iterations=1000))
        codes = []
        for i in range(8):
            c = self.client()
            c.send(connect_packet("c%d" % i, username="alice", password="pw"))
            codes.append(c.expect(2)[1][1])
        self.assertIn(3, codes)
        self.logged("limit.auth-budget")

    @fixture("protocol.qos3")
    def test_qos3(self):
        self.start()
        c = self.client()
        c.connect("a")
        c.send(pkt(0x36, s16("t") + b"\x00\x01"))
        self.assertTrue(c.closed())
        self.logged("protocol.qos3")

    @fixture("protocol.unexpected-packet")
    def test_unexpected_packet(self):
        self.start()
        c = self.client()
        c.connect("a")
        c.send(pkt(0x20, b"\x00\x00"))
        self.assertTrue(c.closed())
        self.logged("protocol.unexpected-packet")

    # ---- bounds ----------------------------------------------------------

    @fixture("limit.subscriptions-per-client")
    def test_subscriptions_per_client(self):
        self.start("--subscriptions-per-client", "2")
        c = self.client()
        c.connect("a")
        self.assertEqual(c.subscribe([("a", 0), ("b", 0), ("c", 0)]), [0, 0, 0x80])
        self.logged("limit.subscriptions-per-client")

    @fixture("limit.subscriptions-total")
    def test_subscriptions_total(self):
        self.start("--subscriptions-total", "2")
        a = self.client()
        a.connect("a")
        self.assertEqual(a.subscribe([("a", 0)]), [0])
        b = self.client()
        b.connect("b")
        self.assertEqual(b.subscribe([("b", 0), ("c", 0)]), [0, 0x80])
        self.logged("limit.subscriptions-total")

    @fixture("limit.connections")
    def test_connections(self):
        self.start("--max-connections", "2")
        a = self.client()
        a.connect("a")
        b = self.client()
        b.connect("b")
        c = self.client()
        self.assertTrue(c.closed())
        # A place frees when one leaves.
        a.close()
        time.sleep(0.3)
        d = self.client()
        d.connect("d")
        self.logged("limit.connections")

    @fixture("limit.topic-level")
    def test_topic_level(self):
        self.start()
        c = self.client()
        c.connect("a")
        self.assertEqual(c.subscribe([("x" * 65, 0), ("y" * 64, 0)]), [0x80, 0])
        self.logged("limit.topic-level")

    @fixture("limit.will-size")
    def test_will_size(self):
        self.start("--will-bytes", "32")
        c = self.client()
        c.send(connect_packet("a", will=("will/topic", b"m" * 40, 0, False)))
        _, body = c.expect(2)
        self.assertEqual(body[1], 3)
        self.assertTrue(c.closed())
        self.logged("limit.will-size")

    @fixture("limit.queue")
    def test_queue(self):
        self.start("--queue-messages", "2", "--inflight", "1")
        sub = self.client()
        sub.connect("s")
        sub.subscribe([("q", 1)])
        pub = self.client()
        pub.connect("p")
        for i in range(5):
            pub.publish("q", str(i).encode(), qos=1, pid=i + 1)
        # One in flight, one queued; the other three did not fit.
        got = [sub.recv_publish()[1]]
        self.assertEqual(got, [b"0"])
        self.logged("limit.queue", 3)

    @fixture("limit.retained")
    def test_retained(self):
        self.start("--retained-messages", "1")
        sub = self.client()
        sub.connect("s")
        sub.subscribe("#")
        pub = self.client()
        pub.connect("p")
        pub.publish("r/1", b"kept", retain=True)
        pub.publish("r/2", b"not kept", retain=True)
        # Both are delivered live; only the first is retained.
        self.assertEqual(sorted(sub.recv_publish()[1] for _ in range(2)), [b"kept", b"not kept"])
        late = self.client()
        late.connect("late")
        late.subscribe("r/#")
        self.assertEqual(late.recv_publish()[:2], ("r/1", b"kept"))
        with self.assertRaises(TimeoutError):
            late.recv(timeout=0.3)
        # An update of a held topic always succeeds.
        pub.publish("r/1", b"updated", retain=True)
        late2 = self.client()
        late2.connect("late2")
        late2.subscribe("r/#")
        self.assertEqual(late2.recv_publish()[1], b"updated")
        self.logged("limit.retained")

    @fixture("limit.offline-sessions")
    def test_offline_sessions(self):
        self.start("--offline-sessions", "1")
        a = self.client()
        a.connect("first", clean=False)
        a.subscribe([("t", 1)])
        a.close()
        time.sleep(0.2)
        b = self.client()
        b.connect("second", clean=False)
        b.subscribe([("t", 1)])
        b.close()
        time.sleep(0.2)
        # The oldest was evicted: the first client finds no session, the second does.
        c = self.client()
        self.assertEqual(c.connect("first", clean=False), 0)
        d = self.client()
        self.assertEqual(d.connect("second", clean=False), 1)
        self.logged("limit.offline-sessions")

    @fixture("timeout.keepalive")
    def test_keepalive(self):
        self.start()
        c = self.client()
        c.connect("quiet", keepalive=1)
        self.assertTrue(c.closed(timeout=6.0))
        self.logged("timeout.keepalive")

    @fixture("timeout.write-stalled")
    def test_write_stalled(self):
        self.start("--write-stall", "1")
        sub = socket.socket()
        sub.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
        sub.connect(("127.0.0.1", self.broker.port))
        self.addCleanup(sub.close)
        sub.sendall(connect_packet("slow") + subscribe_packet([("f", 0)]))
        time.sleep(0.3)
        pub = self.client()
        pub.connect("p")
        payload = b"z" * 8000
        deadline = time.time() + 20
        closed = False
        # The subscriber never reads: its socket fills, then the broker's queue, and
        # after a second without progress the broker drops it.
        while time.time() < deadline and not closed:
            try:
                for _ in range(50):
                    pub.send(publish_packet("f", payload))
            except OSError:
                break
            time.sleep(0.2)
            found = self.broker.events("refusal", "timeout.write-stalled")
            closed = bool(found)
        self.assertTrue(closed, "the stalled subscriber was never dropped")
        self.logged("timeout.write-stalled")

    @fixture("limit.output-full")
    def test_output_full(self):
        self.start("--max-packet", "1024", "--queue-bytes", "1600", "--inflight", "1")
        sub = self.client()
        sub.connect("s")
        sub.subscribe([("t", 1)])
        pub = self.client()
        pub.connect("p")
        # 21 messages of 64 bytes each fill the room for messages exactly; what is
        # left (256 bytes) cannot hold the SUBACK for 248 filters.
        for i in range(21):
            pub.publish("t", b"m" * 49, qos=1, pid=i + 1)
        time.sleep(0.2)
        sub.send(subscribe_packet([("a", 0)] * 248, pid=9))
        self.assertTrue(sub.closed(timeout=3.0))
        self.logged("limit.output-full")

    # ---- the catalogue ---------------------------------------------------

    def test_the_rules_do_not_reach_the_other_counters(self):
        # `ctr` holds one counter for each rule, from 0, and then the broker's other counters from `k_publishes`: a rule
        # whose index is that or more would share a slot with, say, the publish count, and ordinary traffic would be
        # logged as refusals (found when the table grew to 34 and the counters began at 32).
        import re
        from pathlib import Path
        text = (Path(__file__).resolve().parents[2] / "src" / "tables.cho").read_text()
        first = int(re.search(r"pub fn k_publishes\(\) -> \[\] int \{\n    return (\d+);", text).group(1))
        status, out, err = run("rules")
        tags = [r["tag"] for r in records(out) if r["type"] == "rule"]
        self.assertLessEqual(len(tags), first, "%d rules and the other counters begin at %d" % (len(tags), first))

    def test_every_rule_has_a_fixture(self):
        status, out, err = run("rules")
        self.assertEqual(status, 0)
        tags = [r["tag"] for r in records(out) if r["type"] == "rule"]
        self.assertEqual(len(tags), 34)
        # The TLS listener's rules can only be fired once there is a TLS listener (design section 7d): until it is built they
        # have no fixture, and that is written down here, exactly, so that a rule that gains a fixture, or a new rule that
        # lacks one, fails this test. When the listener lands this set is emptied and the fixtures are `@fixture`s above.
        not_yet_firable = {"limit.tls-connections", "timeout.tls-handshake", "tls.failed"}
        self.assertEqual({tag for tag in tags if tag not in FIXTURES}, not_yet_firable,
                         "rules without a fixture, other than the TLS listener's, which is not built")
        self.assertEqual(sorted(t for t in tags if t in FIXTURES), sorted(FIXTURES), "fixtures without a rule")
        for name in FIXTURES.values():
            self.assertTrue(hasattr(Rules, name))


if __name__ == "__main__":
    unittest.main()
