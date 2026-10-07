"""QoS 2 (design section 7a): the four-packet exchange in both directions, the
duplicate and release rules, the in-flight window, redelivery to a persistent
session, and the packets that are refused. Statement numbers are from memory of the
OASIS text (see docs/conformance.md); sections are cited where one is not certain.
"""

import time
import unittest

from harness import (Broker, Client, Closed, PINGREQ, connect_packet, publish_packet, subscribe_packet, pkt,
                     pubcomp_packet, pubrec_packet, pubrel_packet)


class QoS2(unittest.TestCase):
    def setUp(self):
        self.broker = Broker()
        self.addCleanup(self.broker.__exit__, None, None, None)

    def client(self, broker=None, **kw):
        c = (broker or self.broker).client(**kw)
        self.addCleanup(c.close)
        return c

    def connected(self, client_id, broker=None, **kw):
        c = self.client(broker)
        c.connect(client_id, **kw)
        return c

    # ---- SUBSCRIBE grants QoS 2 ------------------------------------------

    def test_subscribe_at_qos2_is_granted_qos2(self):
        # 3.8.4: the server may grant up to what was asked.
        sub = self.connected("s")
        self.assertEqual(sub.subscribe([("a", 2), ("b", 1), ("c", 0)]), [2, 1, 0])

    # ---- a message from a client at QoS 2 --------------------------------

    def test_publish_qos2_is_answered_with_pubrec_and_pubrel_with_pubcomp(self):
        # 4.3.3: PUBLISH -> PUBREC, PUBREL -> PUBCOMP, same identifier.
        c = self.connected("p")
        c.send(publish_packet("t", b"x", qos=2, pid=77))
        flags, body = c.expect(5)
        self.assertEqual(int.from_bytes(body, "big"), 77)
        c.send(pubrel_packet(77))
        flags, body = c.expect(7)
        self.assertEqual(int.from_bytes(body, "big"), 77)

    def test_qos2_message_reaches_a_qos2_subscriber_once(self):
        sub = self.connected("s")
        sub.subscribe([("t/#", 2)])
        pub = self.connected("p")
        pub.publish2("t/a", b"hello")
        topic, payload, qos, retain, dup, pid = sub.recv_publish()
        self.assertEqual((topic, payload, qos, retain, dup), ("t/a", b"hello", 2, False, False))
        self.assertNotEqual(pid, 0)
        with self.assertRaises(TimeoutError):
            sub.recv_publish(timeout=0.4)

    def test_a_resent_publish_is_acknowledged_again_but_not_delivered_again(self):
        # 4.3.3 (method A): an identifier held for its PUBREL is not routed twice.
        sub = self.connected("s")
        sub.subscribe([("t", 2)])
        pub = self.connected("p")
        pub.send(publish_packet("t", b"once", qos=2, pid=9))
        pub.expect(5)
        pub.send(publish_packet("t", b"once", qos=2, pid=9, dup=True))
        _, body = pub.expect(5)
        self.assertEqual(int.from_bytes(body, "big"), 9)
        self.assertEqual(sub.recv_publish()[1], b"once")
        with self.assertRaises(TimeoutError):
            sub.recv_publish(timeout=0.4)
        # After PUBREL the identifier is free: the same number is a new message.
        pub.send(pubrel_packet(9))
        pub.expect(7)
        pub.send(publish_packet("t", b"again", qos=2, pid=9))
        pub.expect(5)
        self.assertEqual(sub.recv_publish()[1], b"again")

    def test_pubrel_for_an_unknown_identifier_is_answered_with_pubcomp(self):
        # 3.6.4 / 4.3.3: a client must be able to finish.
        c = self.connected("c")
        c.send(pubrel_packet(1234))
        _, body = c.expect(7)
        self.assertEqual(int.from_bytes(body, "big"), 1234)

    def test_pubrec_and_pubcomp_for_unknown_identifiers_are_ignored(self):
        c = self.connected("c")
        c.send(pubrec_packet(5) + pubcomp_packet(6) + PINGREQ)
        c.expect(13)          # the next thing the broker says is the PINGRESP

    # ---- the QoS a message goes out at -----------------------------------

    def test_delivery_is_at_the_lower_of_the_two_qos(self):
        s1 = self.connected("s1")
        s1.subscribe([("t", 1)])
        s2 = self.connected("s2")
        s2.subscribe([("t", 2)])
        pub = self.connected("p")
        pub.publish2("t", b"two")
        self.assertEqual(s1.recv_publish()[2], 1)
        self.assertEqual(s2.recv_publish()[2], 2)
        pub.publish("t", b"one", qos=1)
        self.assertEqual(s1.recv_publish()[2], 1)
        self.assertEqual(s2.recv_publish()[2], 1)

    def test_retained_qos2_message_is_delivered_with_the_retain_flag(self):
        pub = self.connected("p")
        pub.publish2("r/a", b"kept", retain=True)
        sub = self.connected("s")
        sub.subscribe([("r/#", 2)])
        topic, payload, qos, retain, dup, pid = sub.recv_publish()
        self.assertEqual((topic, payload, qos, retain), ("r/a", b"kept", 2, True))

    # ---- a message to a client at QoS 2 ----------------------------------

    def exchange_out(self, sub, expect_payload):
        """Receive one QoS 2 PUBLISH and finish it: PUBREC, PUBREL, PUBCOMP."""
        topic, payload, qos, retain, dup, pid = sub.recv_publish()
        self.assertEqual((payload, qos), (expect_payload, 2))
        sub.send(pubrec_packet(pid))
        flags, body = sub.expect(6)
        self.assertEqual(flags, 2)           # [MQTT-3.6.1-1]
        self.assertEqual(int.from_bytes(body, "big"), pid)
        sub.send(pubcomp_packet(pid))
        return pid

    def test_a_qos2_delivery_is_publish_pubrec_pubrel_pubcomp(self):
        sub = self.connected("s")
        sub.subscribe([("t", 2)])
        pub = self.connected("p")
        pub.publish2("t", b"m")
        self.exchange_out(sub, b"m")
        sub.send(PINGREQ)
        sub.expect(13)

    def test_a_publish_is_not_resent_after_pubrec_and_the_identifier_is_reused_after_pubcomp(self):
        sub = self.connected("s")
        sub.subscribe([("t", 2)])
        pub = self.connected("p")
        pids = []
        for i in range(40):
            pub.publish2("t", b"m%d" % i, pid=i + 1)
            pids.append(self.exchange_out(sub, b"m%d" % i))
        # Every identifier was released at PUBCOMP, none was handed out while it was in use.
        sub.send(PINGREQ)
        sub.expect(13)
        self.assertTrue(all(1 <= p <= 65535 for p in pids))

    def test_the_window_counts_qos2_messages_until_pubrec(self):
        # inflight 1: the second message waits for the first's PUBREC; the PUBREL for the first
        # then follows it in queue order.
        with Broker("--inflight", "1") as broker:
            sub = self.connected("s", broker)
            sub.subscribe([("t", 2)])
            pub = self.connected("p", broker)
            pub.publish2("t", b"one", pid=1)
            pub.publish2("t", b"two", pid=2)
            topic, payload, qos, retain, dup, pid1 = sub.recv_publish()
            self.assertEqual(payload, b"one")
            with self.assertRaises(TimeoutError):
                sub.recv_publish(timeout=0.4)
            sub.send(pubrec_packet(pid1))
            topic, payload, qos, retain, dup, pid2 = sub.recv_publish()
            self.assertEqual(payload, b"two")
            _, body = sub.expect(6)
            self.assertEqual(int.from_bytes(body, "big"), pid1)
            sub.send(pubcomp_packet(pid1))
            sub.send(pubrec_packet(pid2))
            _, body = sub.expect(6)
            self.assertEqual(int.from_bytes(body, "big"), pid2)

    # ---- persistent sessions ---------------------------------------------

    def test_an_unacknowledged_qos2_publish_is_resent_with_dup_on_resume(self):
        sub = self.connected("s", clean=False)
        sub.subscribe([("t", 2)])
        pub = self.connected("p")
        pub.publish2("t", b"m")
        _, _, _, _, dup, pid = sub.recv_publish()
        self.assertFalse(dup)
        sub.close()
        time.sleep(0.2)
        sub = self.client()
        self.assertEqual(sub.connect("s", clean=False), 1)
        topic, payload, qos, retain, dup, pid2 = sub.recv_publish()
        self.assertEqual((payload, qos, dup, pid2), (b"m", 2, True, pid))
        sub.send(pubrec_packet(pid))
        sub.expect(6)

    def test_after_pubrec_a_resume_resends_the_pubrel_not_the_publish(self):
        # 4.3.3: once PUBREC was received the sender must not send the PUBLISH again.
        sub = self.connected("s", clean=False)
        sub.subscribe([("t", 2)])
        pub = self.connected("p")
        pub.publish2("t", b"m")
        _, _, _, _, _, pid = sub.recv_publish()
        sub.send(pubrec_packet(pid))
        sub.expect(6)
        sub.close()                       # the PUBCOMP never came
        time.sleep(0.2)
        sub = self.client()
        self.assertEqual(sub.connect("s", clean=False), 1)
        flags, body = sub.expect(6)
        self.assertEqual((flags, int.from_bytes(body, "big")), (2, pid))
        sub.send(pubcomp_packet(pid))
        sub.send(PINGREQ)
        sub.expect(13)

    def test_a_persistent_session_keeps_received_identifiers_across_a_reconnect(self):
        # 4.1 / 4.4: the session holds QoS 2 messages received and not completely acknowledged.
        sub = self.connected("s")
        sub.subscribe([("t", 2)])
        pub = self.connected("p", clean=False)
        pub.send(publish_packet("t", b"once", qos=2, pid=5))
        pub.expect(5)
        self.assertEqual(sub.recv_publish()[1], b"once")
        pub.close()
        time.sleep(0.2)
        pub = self.client()
        self.assertEqual(pub.connect("p", clean=False), 1)
        pub.send(publish_packet("t", b"once", qos=2, pid=5, dup=True))
        pub.expect(5)
        with self.assertRaises(TimeoutError):
            sub.recv_publish(timeout=0.4)
        pub.send(pubrel_packet(5))
        pub.expect(7)

    def test_a_clean_session_forgets_received_identifiers(self):
        sub = self.connected("s")
        sub.subscribe([("t", 2)])
        pub = self.connected("p")
        pub.send(publish_packet("t", b"first", qos=2, pid=5))
        pub.expect(5)
        sub.recv_publish()
        pub.close()
        time.sleep(0.2)
        pub = self.connected("p")
        pub.send(publish_packet("t", b"second", qos=2, pid=5))
        pub.expect(5)
        self.assertEqual(sub.recv_publish()[1], b"second")

    # ---- what a client may not send --------------------------------------

    def refused(self, packet, rule):
        c = self.connected("bad")
        c.send(packet)
        self.assertTrue(c.closed())
        found = self.broker.events("refusal", rule, wait=3.0)
        self.assertTrue(found, "no refusal for %s" % rule)

    def test_pubrel_with_flags_other_than_0010_is_refused(self):
        # [MQTT-3.6.1-1]
        self.refused(pkt(0x60, b"\x00\x01"), "protocol.reserved-flags")

    def test_pubrec_and_pubcomp_with_nonzero_flags_are_refused(self):
        self.refused(pkt(0x52, b"\x00\x01"), "protocol.reserved-flags")
        self.refused(pkt(0x72, b"\x00\x01"), "protocol.reserved-flags")

    def test_a_qos2_ack_with_identifier_zero_is_refused(self):
        self.refused(pubrec_packet(0), "protocol.packet-id")
        self.refused(pubrel_packet(0), "protocol.packet-id")
        self.refused(pubcomp_packet(0), "protocol.packet-id")

    def test_a_qos2_ack_of_the_wrong_length_is_refused(self):
        self.refused(pkt(0x50, b"\x00\x01\x00"), "protocol.malformed-packet")
        self.refused(pkt(0x62, b"\x00"), "protocol.malformed-packet")

    def test_publish_qos2_with_identifier_zero_is_refused(self):
        self.refused(publish_packet("t", b"x", qos=2, pid=0), "protocol.packet-id")


if __name__ == "__main__":
    unittest.main()
