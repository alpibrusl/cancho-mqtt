"""Differential behaviour against Mosquitto (gate G3, issues #8 and #10): the same
scripted scenario is run against this broker and against Mosquitto, and what each
client observes is compared, not the bytes on the wire. Scenarios are written out
(the specification's statements that the two should agree on) and generated from
fixed seeds: connects, wills, subscriptions, publishes at QoS 0 and 1 with and
without retain, unsubscribes, clean and abrupt disconnects, persistent sessions.

Every client acknowledges what it receives at once, so the DUP flag and in-flight
windows do not make the two brokers differ for reasons that are not in the
specification. Where the two legitimately differ, the difference is named in
`KNOWN_DIFFERENCES` and asserted as a difference: a difference nobody wrote down is
a failure, and so is a written-down one that went away.

Skipped, with the reason, where `mosquitto` is not installed; CI installs it."""

import os
import random
import shutil
import subprocess
import tempfile
import time
import unittest

from harness import (Broker, Client, Closed, DISCONNECT, PINGREQ, connect_packet, free_port, puback_packet,
                     pubcomp_packet, publish_packet, pubrec_packet, pubrel_packet, subscribe_packet,
                     unsubscribe_packet)

MOSQUITTO = shutil.which("mosquitto")

TOPICS = ["a", "a/b", "a/b/c", "b", "$d/x", "a//b", "/"]
FILTERS = ["a", "a/+", "+", "#", "a/#", "+/b", "a/b/#", "$d/#", "+/+/c", "/", "a//b"]
NAMES = ["A", "B", "C"]


class MosquittoBroker:
    def __init__(self, sys_interval=None, users=None):
        """`users`: (name, password) pairs for `password_file`, with anonymous clients refused."""
        self.port = free_port()
        self.dir = tempfile.mkdtemp()
        conf = os.path.join(self.dir, "m.conf")
        with open(conf, "w") as f:
            if users:
                # Mosquitto, started as root, runs as its own user: the directory and the file must be readable.
                os.chmod(self.dir, 0o755)
                passwords = os.path.join(self.dir, "passwords")
                for i, (name, password) in enumerate(users):
                    subprocess.run(["mosquitto_passwd", "-b"] + (["-c"] if i == 0 else []) + [passwords, name, password],
                                   check=True, capture_output=True)
                os.chmod(passwords, 0o644)
                f.write("listener %d 127.0.0.1\nallow_anonymous false\npassword_file %s\npersistence false\nlog_type none\n"
                        % (self.port, passwords))
            else:
                f.write("listener %d 127.0.0.1\nallow_anonymous true\npersistence false\nlog_type none\n" % self.port)
            if sys_interval:
                f.write("sys_interval %d\n" % sys_interval)
        self.proc = subprocess.Popen([MOSQUITTO, "-c", conf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        end = time.time() + 5
        while time.time() < end:
            try:
                Client(self.port).close()
                return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("mosquitto did not start")

    def stop(self):
        self.proc.kill()
        self.proc.wait()
        shutil.rmtree(self.dir, ignore_errors=True)


class Run:
    """One scenario against one broker: the clients, and what each saw."""

    def __init__(self, port):
        self.port = port
        self.live = {}
        self.history = {n: [] for n in NAMES}

    def _collect(self, name, client, want):
        """Read until a packet of type `want`; PUBLISHes on the way are recorded and acknowledged."""
        while True:
            k, flags, body = client.recv(3.0)
            if k == 3:
                tl = int.from_bytes(body[:2], "big")
                topic = body[2:2 + tl].decode()
                qos = (flags >> 1) & 3
                at = 2 + tl
                pid = 0
                if qos:
                    pid = int.from_bytes(body[at:at + 2], "big")
                    at += 2
                # The DUP flag is not compared: whether a message that was queued on a
                # connection that closed before it was acknowledged goes out again with DUP
                # is where the brokers differ without either being wrong (test_protocol.py
                # tests redelivery with DUP on its own).
                self.history[name].append(("pub", topic, body[at:], qos, bool(flags & 1)))
                if qos == 1:
                    client.send(puback_packet(pid))
                elif qos == 2:
                    client.send(pubrec_packet(pid))
            elif k == 6:
                # The PUBREL of a QoS 2 delivery: finish it.
                client.send(pubcomp_packet(int.from_bytes(body, "big")))
            elif k == want:
                return body
            else:
                raise AssertionError("unexpected packet type %d" % k)

    def sync(self):
        # Two round trips: a QoS 2 delivery is PUBLISH, PUBREC, PUBREL, PUBCOMP, and the
        # PUBREL follows the PUBREC, so the second PINGRESP is behind every packet a first
        # round caused. Without it a scenario could close a connection with a PUBREL still on
        # its way, and Mosquitto's will handling then differs from run to run (seen: the will of
        # a persistent session delivered to its own reconnection in some runs and not others).
        for _ in range(2):
            for name, client in list(self.live.items()):
                client.send(PINGREQ)
                try:
                    self._collect(name, client, 13)
                except (Closed, TimeoutError):
                    self.live.pop(name)
                    self.history[name].append(("closed",))

    def connect(self, name, clean, will=None, legacy=False):
        if name in self.live:
            old = self.live.pop(name)
            # a takeover: the old connection is closed by the broker
            self.takeover = old
        c = Client(self.port)
        if legacy:
            c.send(connect_packet(name, clean=clean, keepalive=0, will=will, name=b"MQIsdp", level=3))
        else:
            c.send(connect_packet(name, clean=clean, keepalive=0, will=will))
        k, _, body = c.recv(3.0)
        assert k == 2
        self.history[name].append(("connack", body[0] & 1, body[1]))
        self.live[name] = c
        self.sync()

    def subscribe(self, name, filters):
        c = self.live[name]
        c.send(subscribe_packet(filters, pid=5))
        body = self._collect(name, c, 9)
        self.history[name].append(("suback", tuple(body[2:])))
        self.sync()

    def unsubscribe(self, name, filters):
        c = self.live[name]
        c.send(unsubscribe_packet(filters, pid=6))
        self._collect(name, c, 11)
        self.history[name].append(("unsuback",))
        self.sync()

    def publish(self, name, topic, payload, qos, retain):
        c = self.live[name]
        c.send(publish_packet(topic, payload, qos=qos, retain=retain, pid=9))
        if qos == 1:
            self._collect(name, c, 4)
        elif qos == 2:
            self._collect(name, c, 5)
            c.send(pubrel_packet(9))
            self._collect(name, c, 7)
        self.sync()

    def begin2(self, name, topic, payload, dup):
        """A QoS 2 PUBLISH, answered by PUBREC and not yet released."""
        c = self.live[name]
        c.send(publish_packet(topic, payload, qos=2, pid=9, dup=dup))
        self._collect(name, c, 5)
        self.sync()

    def release2(self, name):
        c = self.live[name]
        c.send(pubrel_packet(9))
        self._collect(name, c, 7)
        self.sync()

    def disconnect(self, name, clean):
        c = self.live.pop(name)
        if clean:
            c.send(DISCONNECT)
        c.close()
        time.sleep(0.05)
        self.sync()

    def close(self):
        for c in self.live.values():
            c.close()

    def normalised(self):
        out = {}
        for name, events in self.history.items():
            norm = []
            run = []
            for e in events:
                if e[0] == "pub" and e[4]:
                    run.append(e)
                    continue
                norm.extend(sorted(run))
                run = []
                norm.append(e)
            norm.extend(sorted(run))
            out[name] = norm
        return out


def apply(run, op):
    kind = op[0]
    if kind == "connect":
        run.connect(op[1], op[2], op[3] if len(op) > 3 else None)
    elif kind == "connect31":
        run.connect(op[1], op[2], op[3] if len(op) > 3 else None, legacy=True)
    elif kind == "subscribe":
        run.subscribe(op[1], op[2])
    elif kind == "unsubscribe":
        run.unsubscribe(op[1], op[2])
    elif kind == "publish":
        run.publish(*op[1:])
    elif kind == "disconnect":
        run.disconnect(op[1], op[2])
    elif kind == "begin2":
        run.begin2(*op[1:])
    elif kind == "release2":
        run.release2(op[1])
    else:
        raise ValueError(kind)


def generate(seed, length=30):
    rng = random.Random(seed)
    ops = []
    live = set()
    # One subscription QoS per client for the whole scenario: where one client's
    # overlapping filters have different QoS, the two brokers differ on purpose
    # (KNOWN_DIFFERENCES below), and a generated scenario should test the rest.
    sub_qos = {n: rng.randrange(3) for n in NAMES}
    for _ in range(length):
        name = rng.choice(NAMES)
        r = rng.random()
        if name not in live or r < 0.08:
            will = None
            if rng.random() < 0.3:
                will = (rng.choice(TOPICS), b"will-" + name.encode(), rng.randrange(2), rng.random() < 0.3)
            ops.append((rng.choice(["connect", "connect", "connect31"]), name, rng.random() < 0.5, will))
            live.add(name)
        elif r < 0.30:
            ops.append(("subscribe", name, [(rng.choice(FILTERS), sub_qos[name]) for _ in range(rng.randrange(1, 3))]))
        elif r < 0.38:
            ops.append(("unsubscribe", name, [rng.choice(FILTERS)]))
        elif r < 0.85:
            ops.append(("publish", name, rng.choice(TOPICS), b"m%d" % rng.randrange(1000), rng.randrange(3),
                        rng.random() < 0.25))
        else:
            ops.append(("disconnect", name, rng.random() < 0.5))
            live.discard(name)
    return ops


def play(port, ops):
    run = Run(port)
    try:
        for op in ops:
            apply(run, op)
        run.sync()
    finally:
        run.close()
    return run.normalised()


# Scenarios the two brokers must agree on, written out.
SCRIPTS = {
    "fan-out and filters": [
        ("connect", "A", True), ("connect", "B", True),
        ("subscribe", "A", [("a/+", 1), ("#", 0)]), ("subscribe", "B", [("a/b", 1)]),
        ("publish", "B", "a/b", b"1", 1, False), ("publish", "B", "a/b/c", b"2", 0, False),
        ("publish", "A", "$d/x", b"3", 1, False),
    ],
    "retained lifecycle": [
        ("connect", "A", True),
        ("publish", "A", "a/b", b"keep", 1, True), ("publish", "A", "a", b"other", 0, True),
        ("connect", "B", True), ("subscribe", "B", [("a/#", 1)]),
        ("publish", "A", "a/b", b"", 0, True), ("connect", "C", True), ("subscribe", "C", [("a/#", 0)]),
    ],
    "persistent session": [
        ("connect", "A", False), ("subscribe", "A", [("a/#", 1)]), ("disconnect", "A", True),
        ("connect", "B", True), ("publish", "B", "a/x", b"queued", 1, False), ("publish", "B", "a/y", b"zero", 0, False),
        ("connect", "A", False), ("disconnect", "A", False), ("connect", "A", True),
    ],
    "will on abrupt close only": [
        ("connect", "B", True), ("subscribe", "B", [("w", 1)]),
        ("connect", "A", True, ("w", b"abrupt", 1, False)), ("disconnect", "A", False),
        ("connect", "A", True, ("w", b"clean", 1, False)), ("disconnect", "A", True),
    ],
    "qos2 fan-out and downgrade": [
        ("connect", "A", True), ("connect", "B", True), ("connect", "C", True),
        ("subscribe", "A", [("q/#", 2)]), ("subscribe", "B", [("q/#", 1)]), ("subscribe", "C", [("q/+", 0)]),
        ("publish", "C", "q/x", b"two", 2, False), ("publish", "C", "q/x", b"one", 1, False),
        ("publish", "C", "q/x", b"zero", 0, False),
    ],
    "qos2 retained": [
        ("connect", "A", True), ("publish", "A", "r/a", b"kept", 2, True),
        ("connect", "B", True), ("subscribe", "B", [("r/#", 2)]),
        ("connect", "C", True), ("subscribe", "C", [("r/#", 1)]),
        ("publish", "A", "r/a", b"", 2, True), ("disconnect", "C", True), ("connect", "C", True),
        ("subscribe", "C", [("r/#", 2)]),
    ],
    "qos2 to a persistent session": [
        ("connect", "A", False), ("subscribe", "A", [("p/#", 2)]), ("disconnect", "A", True),
        ("connect", "B", True), ("publish", "B", "p/x", b"queued", 2, False), ("publish", "B", "p/y", b"one", 1, False),
        ("connect", "A", False), ("disconnect", "A", False), ("connect", "A", True),
    ],
    "a resent qos2 publish is routed once": [
        ("connect", "A", True), ("connect", "B", True), ("subscribe", "B", [("t", 2)]),
        ("begin2", "A", "t", b"once", False), ("begin2", "A", "t", b"once", True), ("release2", "A"),
        ("begin2", "A", "t", b"twice", False), ("release2", "A"),
    ],
    "mqtt 3.1 clients": [
        ("connect31", "A", True), ("connect", "B", True), ("subscribe", "A", [("l/#", 1)]), ("subscribe", "B", [("l/#", 1)]),
        ("publish", "B", "l/x", b"to-old", 1, False), ("publish", "A", "l/y", b"from-old", 1, False),
        ("publish", "A", "l/r", b"kept", 1, True), ("connect31", "C", True), ("subscribe", "C", [("l/r", 1)]),
    ],
    "mqtt 3.1 persistent session": [
        ("connect31", "A", False), ("subscribe", "A", [("p/#", 1)]), ("disconnect", "A", True),
        ("connect", "B", True), ("publish", "B", "p/x", b"queued", 1, False),
        ("connect31", "A", False), ("disconnect", "A", False), ("connect31", "A", True),
    ],
    "takeover": [
        ("connect", "A", True, ("w", b"never", 0, False)), ("subscribe", "A", [("t", 0)]),
        ("connect", "B", True), ("subscribe", "B", [("w", 0)]),
        ("connect", "A", True), ("publish", "B", "t", b"x", 0, False),
    ],
}


# Where this broker and Mosquitto legitimately differ. Each is run against both, and
# the difference itself is asserted: one nobody wrote down fails the generated
# scenarios, and one written down that went away fails here.
KNOWN_DIFFERENCES = {
    # A client whose overlapping filters grant different QoS gets one copy. This broker
    # delivers it at the highest QoS granted (3.3.5); Mosquitto 2.0.18 at the QoS of
    # the first match in its trie (`a/#` before `+/b`), whichever was subscribed first.
    "overlapping filters with different QoS": (
        [("connect", "A", True), ("connect", "B", True),
         ("subscribe", "A", [("a/#", 0), ("+/b", 1)]),
         ("publish", "B", "a/b", b"x", 1, False)],
        {"A": [("connack", 0, 0), ("suback", (0, 1)), ("pub", "a/b", b"x", 1, False)], "B": [("connack", 0, 0)]},
        {"A": [("connack", 0, 0), ("suback", (0, 1)), ("pub", "a/b", b"x", 0, False)], "B": [("connack", 0, 0)]},
    ),
    # Method A (design section 7a): this broker routes a QoS 2 message when its PUBLISH arrives
    # and remembers the identifier until PUBREL; Mosquitto holds the message until PUBREL
    # (method B), so a subscriber sees it only after the release. 4.3.3 allows both.
    "qos2 delivery before PUBREL": (
        [("connect", "A", True), ("connect", "B", True), ("subscribe", "B", [("t", 2)]),
         ("begin2", "A", "t", b"early", False)],
        {"A": [("connack", 0, 0)], "B": [("connack", 0, 0), ("suback", (2,)), ("pub", "t", b"early", 2, False)]},
        {"A": [("connack", 0, 0)], "B": [("connack", 0, 0), ("suback", (2,))]},
    ),
}


@unittest.skipUnless(MOSQUITTO, "mosquitto is not installed")
class Differential(unittest.TestCase):
    def both(self, ops):
        mine = Broker()
        theirs = MosquittoBroker()
        try:
            return play(mine.port, ops), play(theirs.port, ops)
        finally:
            mine.__exit__(None, None, None)
            theirs.stop()

    def agree(self, ops, label):
        mine, theirs = self.both(ops)
        self.assertEqual(mine, theirs, "the brokers disagree on %s:\n%s" % (label, "\n".join(map(str, ops))))

    def sys_view(self, port):
        """Fixed traffic, then everything an observer sees under `$SYS/broker/`: the first message of each
        topic with its flags, and the last payload of each."""
        a = Client(port)
        a.connect("A")
        a.subscribe([("t/#", 1)])
        b = Client(port)
        b.connect("B")
        for i in range(3):
            b.publish("t/x", b"hello", qos=0)
        b.publish("t/y", b"world!", qos=1)
        b.publish("keep", b"r", qos=0, retain=True)
        b.send(publish_packet("$SYS/broker/spoof", b"forged", qos=0))
        time.sleep(0.3)
        c = Client(port)
        c.connect("C")
        c.send(subscribe_packet([("$SYS/broker/#", 1)]))
        first, last = {}, {}
        end = time.time() + 3.5
        while time.time() < end:
            try:
                k, f, body = c.recv(1.0)
            except TimeoutError:
                continue
            if k != 3:
                continue
            tl = int.from_bytes(body[:2], "big")
            topic = body[2:2 + tl].decode()
            qos = (f >> 1) & 3
            at = 2 + tl
            if qos:
                c.send(puback_packet(int.from_bytes(body[at:at + 2], "big")))
                at += 2
            first.setdefault(topic, (qos, bool(f & 1)))
            last[topic] = body[at:].decode()
        for x in (a, b, c):
            x.close()
        return first, last

    def test_sys_topics_agree_where_they_are_defined(self):
        mine = Broker("--sys-interval", "1")
        theirs = MosquittoBroker(sys_interval=1)
        try:
            ours_first, ours_last = self.sys_view(mine.port)
            their_first, their_last = self.sys_view(theirs.port)
        finally:
            mine.__exit__(None, None, None)
            theirs.stop()
        self.assertEqual(len(ours_first), 17)
        # Every topic of this broker is one Mosquitto has, delivered the same way (QoS 1, retain set the first time).
        self.assertLessEqual(set(ours_first), set(their_first))
        for topic in ours_first:
            if topic.endswith("clients/maximum"):
                # Seen: Mosquitto sends this one first as a live update (retain clear), not as a retained
                # message, because it publishes it only once it has changed. Here it is sent like the others.
                self.assertEqual(ours_first[topic], (1, True))
                self.assertEqual(their_first[topic][0], 1)
                continue
            self.assertEqual(ours_first[topic], their_first[topic], topic)
        # A client's publish under $SYS/ is dropped by both.
        self.assertNotIn("$SYS/broker/spoof", ours_last)
        self.assertNotIn("$SYS/broker/spoof", their_last)
        # The counters whose meaning is the same in both agree on the same traffic.
        for topic in ("clients/connected", "clients/disconnected", "clients/maximum", "clients/total",
                      "publish/messages/received", "publish/bytes/received", "publish/messages/dropped",
                      "subscriptions/count"):
            self.assertEqual(ours_last["$SYS/broker/" + topic], their_last["$SYS/broker/" + topic], topic)
        # Formats: a number, or `N seconds`, or `<name> version X`.
        self.assertRegex(ours_last["$SYS/broker/uptime"], r"^\d+ seconds$")
        self.assertRegex(their_last["$SYS/broker/uptime"], r"^\d+ seconds$")
        self.assertRegex(ours_last["$SYS/broker/version"], r"^\S+ version \S+$")
        self.assertRegex(their_last["$SYS/broker/version"], r"^\S+ version \S+$")

    def test_connect_outcomes_agree_for_every_name_level_and_identifier(self):
        # The CONNACK code (or a close) for each pairing of protocol name, level and identifier,
        # including MQTT 3.1 (`MQIsdp`, level 3), which 3.1.1 broker code could easily get wrong.
        from harness import pkt, s16
        cases = [(b"MQIsdp", 3, b"abc", True), (b"MQIsdp", 3, b"", True), (b"MQIsdp", 3, b"", False),
                 (b"MQIsdp", 3, b"a" * 100, True), (b"MQIsdp", 4, b"x", True), (b"MQIsdp", 5, b"x", True),
                 (b"MQTT", 3, b"x", True), (b"MQTT", 4, b"", True), (b"MQTT", 4, b"", False),
                 (b"MQIsd", 3, b"x", True), (b"MQTT", 4, b"x" * 100, False)]

        def outcome(port, name, level, cid, clean):
            c = Client(port)
            try:
                c.send(pkt(0x10, s16(name) + bytes([level]) + bytes([2 if clean else 0]) + (60).to_bytes(2, "big") + s16(cid)))
                try:
                    k, f, b = c.recv(3.0)
                    return ("connack", b[1])
                except Closed:
                    return "closed"
            finally:
                c.close()

        mine = Broker()
        theirs = MosquittoBroker()
        try:
            for case in cases:
                with self.subTest(case=case):
                    self.assertEqual(outcome(mine.port, *case), outcome(theirs.port, *case))
        finally:
            mine.__exit__(None, None, None)
            theirs.stop()

    def test_scripted_scenarios(self):
        for name, ops in SCRIPTS.items():
            with self.subTest(name):
                self.agree(ops, name)

    def test_known_differences_are_exactly_these(self):
        for name, (ops, ours, theirs) in KNOWN_DIFFERENCES.items():
            with self.subTest(name):
                mine, other = self.both(ops)
                self.assertEqual({k: v for k, v in mine.items() if v}, ours)
                self.assertEqual({k: v for k, v in other.items() if v}, theirs)

    def test_generated_scenarios(self):
        for seed in range(1, 41):
            with self.subTest(seed=seed):
                self.agree(generate(seed), "seed %d" % seed)


if __name__ == "__main__":
    unittest.main()
