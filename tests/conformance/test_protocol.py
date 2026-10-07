"""MQTT 3.1.1 behaviour a v1 broker claims, one test per statement, each naming it
([MQTT-x.y.z-n] is the specification's own number; docs/design.md section 5 says
which are claimed). The tests read what a client sees, not the broker's tables."""

import time
import unittest

from harness import (Broker, Client, Closed, PINGREQ, DISCONNECT, connect_packet, publish_packet,
                     subscribe_packet, unsubscribe_packet, puback_packet, pkt, s16)


class Protocol(unittest.TestCase):
    def setUp(self):
        self.broker = Broker()
        self.addCleanup(self.broker.__exit__, None, None, None)

    def client(self, **kw):
        c = self.broker.client(**kw)
        self.addCleanup(c.close)
        return c

    def connected(self, client_id, **kw):
        c = self.client()
        c.connect(client_id, **kw)
        return c

    # ---- CONNECT ---------------------------------------------------------

    def test_connack_accepted_without_a_session(self):
        # [MQTT-3.2.2-4] a clean session is acknowledged with session present 0.
        c = self.client()
        self.assertEqual(c.connect("a"), 0)

    def test_empty_client_id_with_clean_session_is_assigned_one(self):
        # [MQTT-3.1.3-6]
        c = self.client()
        self.assertEqual(c.connect(""), 0)

    def test_empty_client_id_without_clean_session_is_refused(self):
        # [MQTT-3.1.3-8] CONNACK 0x02, then close.
        c = self.client()
        c.connect("", clean=False, expect_code=2)
        self.assertTrue(c.closed())

    def test_unsupported_protocol_level_is_refused_with_0x01(self):
        # [MQTT-3.1.2-2]
        c = self.client()
        c.connect("a", level=3, expect_code=1)
        self.assertTrue(c.closed())

    def test_second_connect_closes_the_connection(self):
        # [MQTT-3.1.0-2]
        c = self.connected("a")
        c.send(connect_packet("a"))
        self.assertTrue(c.closed())

    def test_first_packet_must_be_connect(self):
        # [MQTT-3.1.0-1]
        c = self.client()
        c.send(PINGREQ)
        self.assertTrue(c.closed())

    def test_split_packets_are_reassembled(self):
        # a packet delivered one byte at a time is the packet.
        c = self.client()
        for b in connect_packet("slow"):
            c.send(bytes([b]))
            time.sleep(0.002)
        c.expect(2)
        sub = self.connected("sub")
        sub.subscribe("t")
        for b in publish_packet("t", b"x" * 50):
            c.send(bytes([b]))
        topic, payload, *_ = sub.recv_publish()
        self.assertEqual((topic, payload), ("t", b"x" * 50))

    def test_pipelined_packets_are_all_handled(self):
        c = self.client()
        c.send(connect_packet("p") + subscribe_packet([("a/#", 0)]) + PINGREQ + publish_packet("a/b", b"1") + PINGREQ)
        c.expect(2)
        c.expect(9)
        c.expect(13)
        self.assertEqual(c.recv_publish()[:2], ("a/b", b"1"))
        c.expect(13)

    # ---- MQTT 3.1 (protocol name MQIsdp, level 3) ------------------------

    def legacy(self, client_id="old", clean=True, name=b"MQIsdp", level=3):
        c = self.client()
        c.send(connect_packet(client_id, clean=clean, name=name, level=level))
        return c

    def test_mqtt_31_client_connects_publishes_and_subscribes(self):
        # 3.1 differs from 3.1.1 in the CONNECT only (name and level, an identifier is required).
        c = self.legacy("old")
        _, body = c.expect(2)
        self.assertEqual(body[1], 0)
        c.send(subscribe_packet([("t/#", 1)]))
        c.expect(9)
        new = self.connected("new")
        new.publish("t/x", b"hello", qos=1)
        self.assertEqual(c.recv_publish()[:2], ("t/x", b"hello"))
        c.send(publish_packet("u/y", b"back", qos=1, pid=3))
        c.expect(4)

    def test_mqtt_31_with_an_empty_client_id_is_refused_with_0x02(self):
        # 3.1 requires an identifier of at least one character; 3.1.1 allows an empty one
        # with a clean session. Mosquitto answers 0x02 either way.
        for clean in (True, False):
            c = self.legacy("", clean=clean)
            _, body = c.expect(2)
            self.assertEqual(body[1], 2)
            self.assertTrue(c.closed())

    def test_mqtt_31_connack_never_sets_session_present(self):
        # 3.1 has no session-present flag; the session is resumed (the queued message arrives)
        # but the flag byte stays 0, as Mosquitto sends it.
        old = self.legacy("keeper", clean=False)
        old.expect(2)
        old.send(subscribe_packet([("q", 1)]))
        old.expect(9)
        old.close()
        time.sleep(0.2)
        self.connected("pub").publish("q", b"while-away", qos=1)
        again = self.legacy("keeper", clean=False)
        _, body = again.expect(2)
        self.assertEqual(body[0], 0)
        self.assertEqual(again.recv_publish()[:2], ("q", b"while-away"))

    def test_mqtt_31_accepts_a_long_client_id(self):
        # 3.1 says 1 to 23 characters and lets a server allow more; so does Mosquitto.
        c = self.legacy("x" * 100)
        _, body = c.expect(2)
        self.assertEqual(body[1], 0)

    def test_a_name_and_level_that_do_not_go_together_are_refused_with_0x01(self):
        for name, level in ((b"MQIsdp", 4), (b"MQIsdp", 5), (b"MQTT", 3), (b"MQTT", 5)):
            c = self.legacy("x", name=name, level=level)
            _, body = c.expect(2)
            self.assertEqual((name, level, body[1]), (name, level, 1))
            self.assertTrue(c.closed())

    def test_a_protocol_name_that_is_neither_is_closed(self):
        c = self.legacy("x", name=b"MQIsd", level=3)
        self.assertTrue(c.closed())

    # ---- keepalive -------------------------------------------------------

    def test_pingreq_is_answered(self):
        # [MQTT-3.12.4-1]
        c = self.connected("a")
        c.send(PINGREQ)
        c.expect(13)

    def test_silent_client_is_dropped_after_one_and_a_half_keepalives(self):
        # [MQTT-3.1.2-24] and its will is published.
        w = self.connected("watcher")
        w.subscribe("will/#")
        c = self.client()
        c.connect("quiet", keepalive=1, will=("will/quiet", b"gone", 0, False))
        start = time.time()
        self.assertTrue(c.closed(timeout=6.0))
        elapsed = time.time() - start
        self.assertGreaterEqual(elapsed, 1.4)
        self.assertLessEqual(elapsed, 4.0)
        self.assertEqual(w.recv_publish(timeout=3)[:2], ("will/quiet", b"gone"))

    def test_keepalive_zero_is_never_dropped(self):
        c = self.connected("a", keepalive=0)
        time.sleep(2.5)
        c.send(PINGREQ)
        c.expect(13)

    # ---- publish and subscribe ------------------------------------------

    def test_qos0_reaches_a_matching_subscriber_only(self):
        sub = self.connected("s")
        sub.subscribe("a/+/c")
        pub = self.connected("p")
        pub.publish("a/b/c", b"yes")
        pub.publish("a/b/d", b"no")
        pub.publish("a/b/c", b"again")
        self.assertEqual(sub.recv_publish()[:2], ("a/b/c", b"yes"))
        self.assertEqual(sub.recv_publish()[:2], ("a/b/c", b"again"))

    def test_qos1_is_acknowledged_after_it_is_handed_on(self):
        # [MQTT-3.3.4-1] PUBACK for a QoS 1 PUBLISH, with its packet id.
        sub = self.connected("s")
        sub.subscribe([("q", 1)])
        pub = self.connected("p")
        pub.publish("q", b"m", qos=1, pid=77)
        topic, payload, qos, retain, dup, pid = sub.recv_publish()
        self.assertEqual((topic, payload, qos, retain, dup), ("q", b"m", 1, False, False))
        self.assertGreater(pid, 0)

    def test_delivery_qos_is_the_lower_of_publish_and_grant(self):
        # [MQTT-3.8.4-6] and 3.3.5: min(publish QoS, granted QoS).
        sub = self.connected("s")
        self.assertEqual(sub.subscribe([("lo", 0), ("hi", 1)]), [0, 1])
        pub = self.connected("p")
        pub.publish("lo", b"a", qos=1)
        pub.publish("hi", b"b", qos=0)
        pub.publish("hi", b"c", qos=1)
        got = [sub.recv_publish() for _ in range(3)]
        self.assertEqual([(g[0], g[2]) for g in got], [("lo", 0), ("hi", 0), ("hi", 1)])

    def test_suback_has_a_code_per_filter_in_order(self):
        # [MQTT-3.9.3-1]
        sub = self.connected("s")
        self.assertEqual(sub.subscribe([("a", 0), ("bad/#/x", 0), ("c", 1)]), [0, 0x80, 1])

    def test_overlapping_filters_deliver_one_copy_at_the_highest_qos(self):
        # 3.3.5 allows one copy; design section 9 chooses it.
        sub = self.connected("s")
        sub.subscribe([("a/b", 0), ("a/+", 1), ("#", 0)])
        pub = self.connected("p")
        pub.publish("a/b", b"x", qos=1)
        topic, payload, qos, *_ = sub.recv_publish()
        self.assertEqual((topic, qos), ("a/b", 1))
        with self.assertRaises(TimeoutError):
            sub.recv(timeout=0.4)

    def test_dollar_topics_are_not_matched_by_leading_wildcards(self):
        # [MQTT-4.7.2-1]
        sub = self.connected("s")
        sub.subscribe([("#", 0), ("+/x", 0), ("$SYS/#", 0)])
        pub = self.connected("p")
        pub.publish("$SYS/x", b"1")
        pub.publish("a/x", b"2")
        got = [sub.recv_publish()[:2] for _ in range(2)]
        self.assertEqual(sorted(got), [("$SYS/x", b"1"), ("a/x", b"2")][::-1] if False else sorted([("$SYS/x", b"1"), ("a/x", b"2")]))
        with self.assertRaises(TimeoutError):
            sub.recv(timeout=0.3)

    def test_unsubscribe_stops_delivery_and_is_acknowledged(self):
        # [MQTT-3.10.4-4]
        sub = self.connected("s")
        sub.subscribe("t")
        sub.send(unsubscribe_packet(["t"], pid=5))
        _, body = sub.expect(11)
        self.assertEqual(int.from_bytes(body, "big"), 5)
        pub = self.connected("p")
        pub.publish("t", b"x")
        with self.assertRaises(TimeoutError):
            sub.recv(timeout=0.3)

    def test_unsubscribe_of_a_filter_never_subscribed_is_acknowledged(self):
        # [MQTT-3.10.4-5]
        c = self.connected("s")
        c.send(unsubscribe_packet(["never"], pid=6))
        c.expect(11)

    def test_resubscribing_replaces_the_subscription(self):
        # [MQTT-3.8.4-3]
        sub = self.connected("s")
        sub.subscribe([("t", 0)])
        sub.subscribe([("t", 1)], pid=2)
        pub = self.connected("p")
        pub.publish("t", b"x", qos=1)
        self.assertEqual(sub.recv_publish()[2], 1)
        with self.assertRaises(TimeoutError):
            sub.recv(timeout=0.3)

    def test_publisher_receives_its_own_message_when_subscribed(self):
        c = self.connected("both")
        c.subscribe("t")
        c.publish("t", b"me")
        self.assertEqual(c.recv_publish()[:2], ("t", b"me"))

    def test_message_order_is_kept_per_subscriber(self):
        # [MQTT-4.6.0-5] on one topic at one QoS.
        sub = self.connected("s")
        sub.subscribe("o")
        pub = self.connected("p")
        for i in range(50):
            pub.publish("o", str(i).encode())
        self.assertEqual([sub.recv_publish()[1] for _ in range(50)], [str(i).encode() for i in range(50)])

    # ---- retained --------------------------------------------------------

    def test_retained_message_is_delivered_to_a_new_subscriber_with_the_flag(self):
        # [MQTT-3.3.1-6] and [MQTT-3.3.1-8].
        pub = self.connected("p")
        pub.publish("r/1", b"kept", retain=True)
        sub = self.connected("s")
        sub.subscribe("r/#")
        self.assertEqual(sub.recv_publish()[:4], ("r/1", b"kept", 0, True))

    def test_retained_message_forwarded_live_has_the_flag_clear(self):
        # [MQTT-3.3.1-9]
        sub = self.connected("s")
        sub.subscribe("r/#")
        pub = self.connected("p")
        pub.publish("r/1", b"live", retain=True)
        self.assertEqual(sub.recv_publish()[:4], ("r/1", b"live", 0, False))

    def test_empty_retained_payload_clears_it(self):
        # [MQTT-3.3.1-10]
        pub = self.connected("p")
        pub.publish("r/1", b"kept", retain=True)
        pub.publish("r/1", b"", retain=True)
        sub = self.connected("s")
        sub.subscribe("r/#")
        with self.assertRaises(TimeoutError):
            sub.recv(timeout=0.4)

    def test_a_new_retained_message_replaces_the_old(self):
        # [MQTT-3.3.1-7]
        pub = self.connected("p")
        pub.publish("r", b"one", retain=True)
        pub.publish("r", b"two", retain=True)
        sub = self.connected("s")
        sub.subscribe("r")
        self.assertEqual(sub.recv_publish()[1], b"two")
        with self.assertRaises(TimeoutError):
            sub.recv(timeout=0.3)

    def test_retained_delivery_is_capped_by_the_granted_qos(self):
        pub = self.connected("p")
        pub.publish("r", b"one", qos=1, retain=True)
        sub = self.connected("s")
        sub.subscribe([("r", 0)])
        self.assertEqual(sub.recv_publish()[2], 0)

    def test_retained_wildcard_subscription_sees_every_match(self):
        pub = self.connected("p")
        for i in range(5):
            pub.publish("w/%d" % i, b"v", retain=True)
        pub.publish("other", b"v", retain=True)
        sub = self.connected("s")
        sub.subscribe("w/+")
        self.assertEqual(sorted(sub.recv_publish()[0] for _ in range(5)), ["w/%d" % i for i in range(5)])
        with self.assertRaises(TimeoutError):
            sub.recv(timeout=0.3)

    # ---- will ------------------------------------------------------------

    def test_will_is_published_when_the_connection_drops(self):
        # [MQTT-3.1.2-8]
        w = self.connected("w")
        w.subscribe("will")
        c = self.client()
        c.connect("dies", will=("will", b"bye", 0, False))
        c.sock.close()
        self.assertEqual(w.recv_publish(timeout=3)[:2], ("will", b"bye"))

    def test_will_is_discarded_by_disconnect(self):
        # [MQTT-3.14.4-3]
        w = self.connected("w")
        w.subscribe("will")
        c = self.client()
        c.connect("leaves", will=("will", b"bye", 0, False))
        c.send(DISCONNECT)
        c.close()
        with self.assertRaises(TimeoutError):
            w.recv(timeout=0.6)

    def test_will_retain_and_qos_are_applied(self):
        # [MQTT-3.1.2-16] the will is published with its own QoS and retain flag.
        w = self.connected("w")
        w.subscribe([("will", 1)])
        c = self.client()
        c.connect("dies", will=("will", b"bye", 1, True))
        c.sock.close()
        topic, payload, qos, retain, *_ = w.recv_publish(timeout=3)
        self.assertEqual((topic, payload, qos, retain), ("will", b"bye", 1, False))
        late = self.connected("late")
        late.subscribe("will")
        self.assertEqual(late.recv_publish()[:4], ("will", b"bye", 0, True))

    def test_a_will_is_published_once(self):
        w = self.connected("w")
        w.subscribe("will")
        c = self.client()
        c.connect("dies", will=("will", b"bye", 0, False))
        c.sock.close()
        w.recv_publish(timeout=3)
        with self.assertRaises(TimeoutError):
            w.recv(timeout=0.5)

    # ---- takeover and sessions ------------------------------------------

    def test_same_client_id_takes_over_and_the_old_connection_closes(self):
        # [MQTT-3.1.4-2]
        a = self.connected("same")
        b = self.client()
        b.connect("same")
        self.assertTrue(a.closed())
        b.send(PINGREQ)
        b.expect(13)

    def test_takeover_publishes_the_old_will(self):
        # [MQTT-3.1.2-8]: the server closes the old connection without a DISCONNECT, so
        # its will is published. (Found by the differential run: Mosquitto does the same.)
        w = self.connected("w")
        w.subscribe("will")
        a = self.client()
        a.connect("same", will=("will", b"x", 0, False))
        b = self.client()
        b.connect("same")
        self.assertEqual(w.recv_publish(timeout=3)[:2], ("will", b"x"))
        with self.assertRaises(TimeoutError):
            w.recv(timeout=0.4)

    def test_takeover_that_continues_a_persistent_session_does_not_publish_the_will(self):
        # The reconnecting client is not gone; Mosquitto does not announce it as gone.
        w = self.connected("w")
        w.subscribe("will")
        a = self.client()
        a.connect("same", clean=False, will=("will", b"x", 0, False))
        b = self.client()
        self.assertEqual(b.connect("same", clean=False), 1)
        with self.assertRaises(TimeoutError):
            w.recv(timeout=0.6)

    def test_persistent_session_keeps_subscriptions_and_queues_qos1(self):
        # [MQTT-3.1.2-4], [MQTT-3.2.2-2], 3.1.2.4: session state kept for clean=0.
        a = self.client()
        self.assertEqual(a.connect("keep", clean=False), 0)
        a.subscribe([("k/#", 1)])
        a.close()
        time.sleep(0.2)
        pub = self.connected("p")
        pub.publish("k/1", b"one", qos=1)
        pub.publish("k/2", b"two", qos=1)
        pub.publish("k/0", b"zero", qos=0)
        b = self.client()
        self.assertEqual(b.connect("keep", clean=False), 1)
        first = b.recv_publish()
        second = b.recv_publish()
        self.assertEqual([first[:3], second[:3]], [("k/1", b"one", 1), ("k/2", b"two", 1)])
        b.send(puback_packet(first[5]))
        b.send(puback_packet(second[5]))
        with self.assertRaises(TimeoutError):
            b.recv(timeout=0.3)

    def test_unacknowledged_qos1_is_redelivered_with_dup_on_resume(self):
        # [MQTT-4.4.0-1]
        a = self.client()
        a.connect("keep", clean=False)
        a.subscribe([("d", 1)])
        pub = self.connected("p")
        pub.publish("d", b"m", qos=1)
        first = a.recv_publish()
        self.assertFalse(first[4])
        a.close()
        time.sleep(0.2)
        b = self.client()
        self.assertEqual(b.connect("keep", clean=False), 1)
        again = b.recv_publish()
        self.assertEqual((again[0], again[1], again[2], again[4]), ("d", b"m", 1, True))
        self.assertEqual(again[5], first[5])

    def test_clean_session_discards_what_was_kept(self):
        # [MQTT-3.1.2-6]
        a = self.client()
        a.connect("keep", clean=False)
        a.subscribe([("k", 1)])
        a.close()
        time.sleep(0.2)
        b = self.client()
        self.assertEqual(b.connect("keep", clean=True), 0)
        pub = self.connected("p")
        pub.publish("k", b"x", qos=1)
        with self.assertRaises(TimeoutError):
            b.recv(timeout=0.4)

    def test_clean_session_leaves_no_session_behind(self):
        a = self.client()
        a.connect("gone", clean=True)
        a.subscribe([("k", 1)])
        a.close()
        time.sleep(0.2)
        b = self.client()
        self.assertEqual(b.connect("gone", clean=False), 0)

    def test_qos1_window_limits_messages_in_flight(self):
        # design section 4: at most --inflight QoS 1 messages sent and unacknowledged.
        broker = Broker("--inflight", "2")
        self.addCleanup(broker.__exit__, None, None, None)
        sub = broker.client()
        self.addCleanup(sub.close)
        sub.connect("s")
        sub.subscribe([("w", 1)])
        pub = broker.client()
        self.addCleanup(pub.close)
        pub.connect("p")
        for i in range(5):
            pub.publish("w", str(i).encode(), qos=1, pid=i + 1)
        got = [sub.recv_publish() for _ in range(2)]
        with self.assertRaises(TimeoutError):
            sub.recv(timeout=0.4)
        sub.send(puback_packet(got[0][5]))
        third = sub.recv_publish()
        self.assertEqual(third[1], b"2")
        sub.send(puback_packet(got[1][5]))
        sub.send(puback_packet(third[5]))
        self.assertEqual([sub.recv_publish()[1] for _ in range(2)], [b"3", b"4"])

    # ---- shape of topics -------------------------------------------------

    def test_publish_to_a_topic_with_a_wildcard_closes_the_connection(self):
        # [MQTT-3.3.2-2]
        c = self.connected("a")
        c.send(publish_packet("a/+", b"x"))
        self.assertTrue(c.closed())

    def test_leading_slash_and_empty_levels_are_distinct_topics(self):
        sub = self.connected("s")
        sub.subscribe([("/a", 0), ("a//b", 0)])
        pub = self.connected("p")
        pub.publish("a", b"1")
        pub.publish("/a", b"2")
        pub.publish("a/b", b"3")
        pub.publish("a//b", b"4")
        self.assertEqual([sub.recv_publish()[1] for _ in range(2)], [b"2", b"4"])

    def test_the_broker_survives_a_client_that_vanishes_mid_packet(self):
        c = self.client()
        c.send(connect_packet("half")[:5])
        c.close()
        d = self.connected("after")
        d.send(PINGREQ)
        d.expect(13)
        self.assertTrue(self.broker.alive())


if __name__ == "__main__":
    unittest.main()
