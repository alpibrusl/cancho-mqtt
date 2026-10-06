"""Interop (issue #10): real clients, not hand-written packets. `paho-mqtt` (the
Python client) and `mosquitto_pub` / `mosquitto_sub` talk to the broker; what they
observe is what the test asserts. Skipped, with the reason, where a client is not
installed; CI installs both."""

import shutil
import subprocess
import threading
import time
import unittest

from harness import Broker

try:
    import paho.mqtt.client as paho
    from paho.mqtt.enums import CallbackAPIVersion
except ImportError:  # pragma: no cover
    paho = None

HAVE_CLI = shutil.which("mosquitto_pub") and shutil.which("mosquitto_sub")


def paho_client(port, client_id, clean=True, will=None):
    c = paho.Client(CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=clean, protocol=paho.MQTTv311)
    c.received = []
    c.acks = threading.Event()
    c.connected = threading.Event()
    c.on_message = lambda cl, ud, m: cl.received.append((m.topic, bytes(m.payload), m.qos, m.retain))
    c.on_connect = lambda cl, ud, flags, rc, props: cl.connected.set()
    c.on_subscribe = lambda cl, ud, mid, rcs, props: cl.acks.set()
    if will:
        c.will_set(*will)
    c.connect("127.0.0.1", port, keepalive=30)
    c.loop_start()
    assert c.connected.wait(5), "paho did not connect"
    return c


def wait_for(cond, seconds=5.0):
    end = time.time() + seconds
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@unittest.skipIf(paho is None, "paho-mqtt is not installed")
class Paho(unittest.TestCase):
    def setUp(self):
        self.broker = Broker()
        self.addCleanup(self.broker.__exit__, None, None, None)
        self.clients = []

    def tearDown(self):
        for c in self.clients:
            try:
                c.disconnect()
            except Exception:
                pass
            c.loop_stop()

    def client(self, *a, **kw):
        c = paho_client(self.broker.port, *a, **kw)
        self.clients.append(c)
        return c

    def test_publish_subscribe_qos0_and_qos1(self):
        sub = self.client("sub")
        sub.subscribe([("t/#", 1)])
        self.assertTrue(sub.acks.wait(5))
        pub = self.client("pub")
        pub.publish("t/a", b"zero", qos=0)
        info = pub.publish("t/b", b"one", qos=1)
        info.wait_for_publish(5)
        self.assertTrue(wait_for(lambda: len(sub.received) == 2))
        self.assertEqual(sorted(sub.received), [("t/a", b"zero", 0, False), ("t/b", b"one", 1, False)])

    def test_retained_and_will(self):
        pub = self.client("pub")
        pub.publish("r", b"kept", qos=1, retain=True).wait_for_publish(5)
        watcher = self.client("watcher")
        watcher.subscribe([("w", 0), ("r", 0)])
        self.assertTrue(wait_for(lambda: watcher.received == [("r", b"kept", 0, True)]))
        dying = self.client("dying", will=("w", b"bye", 0, False))
        dying.loop_stop()
        dying._sock.close()
        self.assertTrue(wait_for(lambda: ("w", b"bye", 0, False) in watcher.received))

    def test_persistent_session_queues_while_offline(self):
        a = self.client("keeper", clean=False)
        a.subscribe([("q", 1)])
        self.assertTrue(a.acks.wait(5))
        a.loop_stop()
        a.disconnect()
        time.sleep(0.2)
        pub = self.client("pub")
        pub.publish("q", b"while-away", qos=1).wait_for_publish(5)
        b = self.client("keeper", clean=False)
        self.assertTrue(wait_for(lambda: b.received == [("q", b"while-away", 1, False)]))

    def test_many_clients_one_topic(self):
        subs = [self.client("s%d" % i) for i in range(30)]
        for s in subs:
            s.subscribe([("fan", 0)])
        time.sleep(0.5)
        pub = self.client("pub")
        for i in range(20):
            pub.publish("fan", str(i).encode(), qos=0)
        self.assertTrue(wait_for(lambda: all(len(s.received) == 20 for s in subs), 10))
        for s in subs:
            self.assertEqual([m[1] for m in s.received], [str(i).encode() for i in range(20)])


@unittest.skipUnless(HAVE_CLI, "mosquitto_pub / mosquitto_sub are not installed")
class MosquittoClients(unittest.TestCase):
    def setUp(self):
        self.broker = Broker()
        self.addCleanup(self.broker.__exit__, None, None, None)

    def test_pub_sub_and_retained(self):
        port = str(self.broker.port)
        sub = subprocess.Popen(["mosquitto_sub", "-h", "127.0.0.1", "-p", port, "-t", "m/#", "-v", "-q", "1", "-C", "3"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(0.4)
        for topic, msg, extra in (("m/a", "one", []), ("m/b", "two", ["-q", "1"]), ("m/c", "three", ["-r"])):
            subprocess.run(["mosquitto_pub", "-h", "127.0.0.1", "-p", port, "-t", topic, "-m", msg, *extra], check=True)
        out, err = sub.communicate(timeout=10)
        self.assertEqual(out.split("\n")[:3], ["m/a one", "m/b two", "m/c three"])
        late = subprocess.run(["mosquitto_sub", "-h", "127.0.0.1", "-p", port, "-t", "m/c", "-C", "1", "-W", "3", "-v"],
                              capture_output=True, text=True, timeout=10)
        self.assertEqual(late.stdout.strip(), "m/c three")

    def test_a_will_through_the_cli(self):
        port = str(self.broker.port)
        watcher = subprocess.Popen(["mosquitto_sub", "-h", "127.0.0.1", "-p", port, "-t", "will/#", "-v", "-C", "1"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        time.sleep(0.4)
        victim = subprocess.Popen(["mosquitto_sub", "-h", "127.0.0.1", "-p", port, "-t", "x", "--will-topic", "will/v",
                                   "--will-payload", "gone"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        time.sleep(0.4)
        victim.kill()
        out, err = watcher.communicate(timeout=10)
        self.assertEqual(out.strip(), "will/v gone")
        victim.communicate()


if __name__ == "__main__":
    unittest.main()
