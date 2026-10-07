"""`$SYS` (design section 7b): the seventeen topics, the retain flag on the first delivery and its
absence on updates, QoS, wildcards, the counters, the interval flag, offline sessions, and a client
that publishes under `$SYS/`. Mosquitto's names and flags are compared in test_differential.py."""

import re
import time
import unittest

from harness import (Broker, Client, PINGREQ, connect_packet, publish_packet, puback_packet, pkt, pubrec_packet,
                     subscribe_packet)

TOPICS = [
    "$SYS/broker/version", "$SYS/broker/uptime", "$SYS/broker/clients/total", "$SYS/broker/clients/connected",
    "$SYS/broker/clients/disconnected", "$SYS/broker/clients/maximum", "$SYS/broker/messages/received",
    "$SYS/broker/messages/sent", "$SYS/broker/publish/messages/received", "$SYS/broker/publish/messages/sent",
    "$SYS/broker/publish/messages/dropped", "$SYS/broker/publish/bytes/received", "$SYS/broker/publish/bytes/sent",
    "$SYS/broker/bytes/received", "$SYS/broker/bytes/sent", "$SYS/broker/subscriptions/count",
    "$SYS/broker/retained messages/count",
]


def read(client, seconds):
    """Every PUBLISH that arrives within `seconds`: (topic, payload, qos, retain). QoS 1 is acknowledged."""
    out = []
    end = time.time() + seconds
    while time.time() < end:
        try:
            k, flags, body = client.recv(max(0.05, end - time.time()))
        except TimeoutError:
            break
        if k != 3:
            continue
        tl = int.from_bytes(body[:2], "big")
        qos = (flags >> 1) & 3
        at = 2 + tl
        if qos:
            client.send(puback_packet(int.from_bytes(body[at:at + 2], "big")))
            at += 2
        out.append((body[2:2 + tl].decode(), body[at:], qos, bool(flags & 1)))
    return out


def latest(messages):
    """The last payload of each topic, as text."""
    return {t: p.decode() for t, p, q, r in messages}


class Sys(unittest.TestCase):
    def start(self, *flags):
        broker = Broker("--sys-interval", "1", "--stats-seconds", "0", *flags)
        self.addCleanup(broker.__exit__, None, None, None)
        return broker

    def connected(self, broker, name, **kw):
        c = broker.client()
        self.addCleanup(c.close)
        c.connect(name, **kw)
        return c

    def test_subscribing_delivers_every_topic_once_with_the_retain_flag_set(self):
        b = self.start()
        c = self.connected(b, "obs")
        c.subscribe([("$SYS/#", 1)])
        first = read(c, 0.6)
        self.assertEqual([t for t, p, q, r in first], TOPICS)
        self.assertTrue(all(r and q == 1 for t, p, q, r in first), first)
        v = latest(first)
        self.assertTrue(v["$SYS/broker/version"].startswith("cancho-mqtt version "))
        self.assertRegex(v["$SYS/broker/uptime"], r"^\d+ seconds$")
        for t in TOPICS[2:]:
            self.assertRegex(v[t], r"^\d+$", t)

    def test_updates_follow_every_interval_with_the_retain_flag_clear(self):
        b = self.start()
        c = self.connected(b, "obs")
        c.subscribe([("$SYS/broker/uptime", 1)])
        got = read(c, 3.6)
        self.assertGreaterEqual(len(got), 3, got)
        self.assertTrue(got[0][3])
        self.assertTrue(all(not r for t, p, q, r in got[1:]), got)
        seconds = [int(p.split()[0]) for t, p, q, r in got]
        self.assertEqual(seconds, sorted(seconds))
        self.assertGreater(seconds[-1], seconds[0])

    def test_a_filter_that_starts_with_a_wildcard_does_not_see_them(self):
        b = self.start()
        c = self.connected(b, "obs")
        c.subscribe([("#", 0), ("+/broker/uptime", 0), ("+/#", 0)])
        self.assertEqual(read(c, 2.3), [])
        d = self.connected(b, "obs2")
        d.subscribe([("$SYS/broker/subscriptions/+", 0)])
        self.assertEqual([t for t, p, q, r in read(d, 0.5)], ["$SYS/broker/subscriptions/count"])

    def test_delivery_is_at_the_lower_of_one_and_the_granted_qos(self):
        b = self.start()
        c = self.connected(b, "obs")
        c.subscribe([("$SYS/broker/uptime", 0)])
        got = read(c, 2.3)
        self.assertGreaterEqual(len(got), 2)
        self.assertTrue(all(q == 0 for t, p, q, r in got))
        d = self.connected(b, "obs2")
        d.subscribe([("$SYS/broker/uptime", 2)])
        self.assertTrue(all(q == 1 for t, p, q, r in read(d, 1.6)))

    def test_an_interval_of_zero_turns_it_off(self):
        broker = Broker("--sys-interval", "0", "--stats-seconds", "0")
        self.addCleanup(broker.__exit__, None, None, None)
        c = broker.client()
        self.addCleanup(c.close)
        c.connect("obs")
        c.subscribe([("$SYS/#", 1)])
        self.assertEqual(read(c, 1.5), [])

    def test_the_counters_follow_the_traffic(self):
        b = self.start()
        a = self.connected(b, "A")
        a.subscribe([("t/#", 1)])
        p = self.connected(b, "B")
        for i in range(3):
            p.publish("t/x", b"hello", qos=0)
        p.publish("t/y", b"world!", qos=1)
        p.publish("keep", b"r", qos=0, retain=True)
        time.sleep(0.2)
        o = self.connected(b, "C")
        o.subscribe([("$SYS/broker/#", 1)])
        v = latest(read(o, 0.6))
        self.assertEqual(v["$SYS/broker/clients/total"], "3")
        self.assertEqual(v["$SYS/broker/clients/connected"], "3")
        self.assertEqual(v["$SYS/broker/clients/disconnected"], "0")
        self.assertEqual(v["$SYS/broker/clients/maximum"], "3")
        self.assertEqual(v["$SYS/broker/publish/messages/received"], "5")
        self.assertEqual(v["$SYS/broker/publish/bytes/received"], str(3 * 5 + 6 + 1))
        self.assertEqual(v["$SYS/broker/retained messages/count"], "1")
        self.assertEqual(v["$SYS/broker/subscriptions/count"], "2")
        self.assertGreater(int(v["$SYS/broker/bytes/received"]), 100)
        self.assertGreater(int(v["$SYS/broker/messages/received"]), 8)

    def test_persistent_sessions_without_a_connection_count_as_disconnected(self):
        b = self.start()
        a = self.connected(b, "A", clean=False)
        a.subscribe([("t", 1)])
        a.close()
        time.sleep(0.3)
        o = self.connected(b, "obs")
        o.subscribe([("$SYS/broker/clients/#", 1)])
        v = latest(read(o, 0.6))
        self.assertEqual((v["$SYS/broker/clients/connected"], v["$SYS/broker/clients/disconnected"],
                          v["$SYS/broker/clients/total"], v["$SYS/broker/clients/maximum"]), ("1", "1", "2", "2"))

    def test_messages_a_subscriber_could_not_take_are_counted_as_dropped(self):
        b = self.start("--queue-messages", "1", "--inflight", "1")
        slow = self.connected(b, "slow")
        slow.subscribe([("t", 1)])            # never acknowledges what it is sent
        p = self.connected(b, "p")
        for i in range(4):
            p.publish("t", b"m%d" % i, qos=1)
        time.sleep(0.2)
        o = self.connected(b, "obs")
        o.subscribe([("$SYS/broker/publish/messages/dropped", 1)])
        got = read(o, 0.5)
        self.assertGreaterEqual(int(got[0][1]), 3, got)

    def test_offline_persistent_sessions_are_not_sent_the_updates(self):
        b = self.start()
        a = self.connected(b, "keeper", clean=False)
        a.subscribe([("$SYS/broker/uptime", 1)])
        read(a, 0.3)
        a.close()
        time.sleep(2.6)
        again = b.client()
        self.addCleanup(again.close)
        self.assertEqual(again.connect("keeper", clean=False), 1)
        self.assertEqual(read(again, 0.4), [])

    def test_they_cost_none_of_the_retained_bound(self):
        b = self.start("--retained-messages", "1")
        p = self.connected(b, "p")
        p.publish("keep", b"1", qos=0, retain=True)
        o = self.connected(b, "obs")
        o.subscribe([("$SYS/#", 1), ("keep", 1)])
        got = read(o, 0.6)
        self.assertEqual(len([m for m in got if m[0].startswith("$SYS/")]), 17)
        self.assertIn(("keep", b"1", 0, True), got)
        self.assertEqual(latest(got)["$SYS/broker/retained messages/count"], "1")

    def test_a_client_cannot_publish_under_sys_and_is_told_nothing_is_wrong(self):
        b = self.start()
        o = self.connected(b, "obs")
        o.subscribe([("$SYS/broker/spoof", 0), ("$other/x", 0)])
        c = self.connected(b, "c")
        c.send(publish_packet("$SYS/broker/spoof", b"zero", qos=0))
        c.send(publish_packet("$SYS/broker/spoof", b"one", qos=1, pid=4))
        _, body = c.expect(4)
        self.assertEqual(int.from_bytes(body, "big"), 4)
        c.send(publish_packet("$SYS/broker/spoof", b"two", qos=2, pid=5))
        _, body = c.expect(5)
        self.assertEqual(int.from_bytes(body, "big"), 5)
        c.send(publish_packet("$other/x", b"fine", qos=0))
        self.assertEqual(read(o, 0.6), [("$other/x", b"fine", 0, False)])
        c.send(PINGREQ)
        c.expect(13)


if __name__ == "__main__":
    unittest.main()
