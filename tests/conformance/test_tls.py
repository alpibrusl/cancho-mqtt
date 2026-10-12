"""TLS 1.3 on the second listener (docs/design.md section 7d, item 10): the protocol over an encrypted connection, the
bounds on handshakes, hostile input before and during the handshake, the identity read at start and again on SIGHUP, and the
refusals when it cannot be.

The broker reads its certificate from a directory fixed at build time, `/etc/cancho/mqtt`. These tests create an identity
there when the directory is empty or already theirs (`harness.own_certs`), and skip when it is somebody's deployment: set
MQTT_REQUIRE_TLS=1 (CI does) to fail instead of skipping."""

import os
import shutil
import signal
import subprocess
import threading
import socket
import ssl
import time
import unittest

import harness
from harness import (Broker, Client, Closed, CERTS, connect_packet, make_identity, publish_packet, records, run, tls_context,
                     user_line)


try:
    import paho.mqtt.client as paho
    from paho.mqtt.enums import CallbackAPIVersion
except ImportError:  # pragma: no cover
    paho = None

HAVE_CLI = shutil.which("mosquitto_pub") and shutil.which("mosquitto_sub")


def setUpModule():
    if not harness.own_certs():
        if os.environ.get("MQTT_REQUIRE_TLS"):
            raise RuntimeError("the TLS tests need to own /etc/cancho/mqtt, and cannot")
        raise unittest.SkipTest("/etc/cancho/mqtt is not empty and not the tests', or cannot be written")


def rss_kib(pid):
    for line in open("/proc/%d/status" % pid):
        if line.startswith("VmRSS:"):
            return int(line.split()[1])
    return 0


class Tls(unittest.TestCase):
    def start(self, *flags, **kw):
        broker = Broker(*flags, tls=True, **kw)
        self.addCleanup(broker.__exit__, None, None, None)
        return broker

    def tls(self, broker, **kw):
        c = broker.tls_client(**kw)
        self.addCleanup(c.close)
        return c

    def raw(self, broker):
        s = socket.create_connection(("127.0.0.1", broker.tls_port), timeout=3)
        self.addCleanup(s.close)
        return s

    # ---- the protocol, encrypted -------------------------------------------------------------------------------

    def test_connect_subscribe_publish(self):
        broker = self.start()
        sub = self.tls(broker)
        self.assertEqual(sub.connect("sub"), 0)
        self.assertEqual(sub.subscribe([("a/#", 1)]), [1])
        pub = self.tls(broker)
        pub.connect("pub")
        pub.publish("a/b", b"hello", qos=1)
        self.assertEqual(sub.recv_publish()[:3], ("a/b", b"hello", 1))
        self.assertTrue(broker.alive())

    def test_the_connection_is_tls_1_3(self):
        broker = self.start()
        c = self.tls(broker)
        self.assertEqual(c.sock.version(), "TLSv1.3")
        c.connect("v")

    def test_plain_and_secure_clients_share_one_broker(self):
        broker = self.start()
        sub = self.tls(broker)
        sub.connect("s")
        sub.subscribe([("x", 0)])
        plain = broker.client()
        plain.connect("p")
        plain.publish("x", b"from plain")
        self.assertEqual(sub.recv_publish()[:2], ("x", b"from plain"))
        plain.subscribe([("y", 0)])
        sub.publish("y", b"from tls")
        self.assertEqual(plain.recv_publish()[:2], ("y", b"from tls"))

    def test_retained_messages_and_qos_2(self):
        broker = self.start()
        a = self.tls(broker)
        a.connect("a")
        a.publish("r/1", b"kept", retain=True)
        a.publish2("q2", b"x")
        b = self.tls(broker)
        b.connect("b")
        b.subscribe([("r/#", 0)])
        self.assertEqual(b.recv_publish()[:4], ("r/1", b"kept", 0, True))

    def test_a_large_payload_and_a_burst_arrive_whole(self):
        broker = self.start("--queue-bytes", "1048576", "--queue-messages", "1024")
        sub = self.tls(broker, timeout=10)
        sub.connect("sub")
        sub.subscribe([("big", 0), ("burst", 0)])
        pub = self.tls(broker, timeout=10)
        pub.connect("pub")
        body = bytes(range(256)) * 60                       # 15,360 bytes: a record's worth and more
        pub.publish("big", body)
        self.assertEqual(sub.recv_publish()[1], body)
        for i in range(300):
            pub.publish("burst", b"%04d" % i + b"." * 100)
        for i in range(300):
            self.assertEqual(sub.recv_publish()[1][:4], b"%04d" % i)

    def test_a_will_is_sent_when_the_peer_drops_without_close_notify(self):
        broker = self.start()
        watcher = broker.client()
        watcher.connect("w")
        watcher.subscribe([("will/#", 0)])
        c = self.tls(broker)
        c.send(connect_packet("willing", will=("will/t", b"gone", 0, False)))
        c.expect(2)
        c.sock.close()                                       # Python sends no close_notify on close
        self.assertEqual(watcher.recv_publish()[:2], ("will/t", b"gone"))

    def test_keepalive_expires_over_tls(self):
        broker = self.start()
        c = self.tls(broker)
        c.connect("quiet", keepalive=1)
        self.assertTrue(c.closed(timeout=5))
        self.assertTrue(broker.events("refusal", "timeout.keepalive", wait=2))

    def test_authentication_over_tls(self):
        broker = self.start(users=user_line("ann", "secret"))
        good = self.tls(broker)
        good.connect("good", username="ann", password="secret")
        bad = self.tls(broker)
        bad.connect("bad", username="ann", password="wrong", expect_code=4)

    def test_a_stop_with_secure_clients_connected_ends_cleanly(self):
        broker = self.start()
        for n in range(3):
            self.tls(broker).connect("c%d" % n)
        status, lines = broker.stop()
        self.assertEqual(status, 0)
        self.assertEqual(lines[-1]["type"], "end")

    # ---- what the engine offers ----------------------------------------------------------------------------------

    def test_a_tls_1_2_only_client_is_refused_with_the_version_alert(self):
        broker = self.start()
        with self.assertRaises(ssl.SSLError) as caught:
            self.tls(broker, context=tls_context(version=ssl.TLSVersion.TLSv1_2))
        self.assertIn("PROTOCOL_VERSION", str(caught.exception).upper())
        self.assertTrue(broker.events("refusal", "tls.failed", wait=3))
        self.tls(broker).connect("after")                   # the broker is unharmed

    def test_alpn(self):
        broker = self.start()
        with self.assertRaises(ssl.SSLError) as caught:
            self.tls(broker, context=tls_context(alpn=["http/1.1"]))
        self.assertIsInstance(caught.exception, ssl.SSLError)       # alert 120, which OpenSSL does not name
        self.assertTrue(broker.events("refusal", "tls.failed", wait=3))
        c = self.tls(broker, context=tls_context(alpn=["mqtt"]))
        self.assertEqual(c.sock.selected_alpn_protocol(), "mqtt")
        c.connect("alpn")
        none = self.tls(broker)                              # no ALPN offered: connects
        none.connect("none")

    # ---- bounds ------------------------------------------------------------------------------------------------

    def test_a_peer_that_sends_nothing_is_closed_at_the_handshake_deadline(self):
        broker = self.start("--tls-handshake-timeout", "1")
        s = self.raw(broker)
        start = time.time()
        s.settimeout(6)
        self.assertEqual(s.recv(16), b"")
        self.assertLess(time.time() - start, 4)
        self.assertTrue(broker.events("refusal", "timeout.tls-handshake", wait=3))
        self.tls(broker).connect("after")

    def test_connections_over_the_engine_slots_are_closed_at_once(self):
        broker = self.start("--tls-connections", "2")
        a = self.tls(broker)
        a.connect("a")
        b = self.tls(broker)
        b.connect("b")
        s = self.raw(broker)
        s.settimeout(3)
        self.assertEqual(s.recv(16), b"")
        self.assertTrue(broker.events("refusal", "limit.tls-connections", wait=3))
        a.close()
        time.sleep(0.5)
        self.tls(broker).connect("c")                        # a place frees when one leaves

    def test_handshakes_over_the_rate_wait_their_turn(self):
        broker = self.start("--tls-rate", "1")
        start = time.time()
        clients = []
        for n in range(3):
            c = self.tls(broker, timeout=8)
            c.connect("r%d" % n)
            clients.append(c)
        self.assertGreaterEqual(time.time() - start, 1.0)    # three starts need three distinct seconds
        self.assertEqual(broker.events("refusal", "limit.tls-connections"), [])
        self.assertEqual(broker.events("refusal", "timeout.tls-handshake"), [])

    def test_handshakes_in_progress_are_bounded(self):
        broker = self.start("--tls-handshakes", "1", "--tls-handshake-timeout", "4")
        silent = self.raw(broker)                            # takes the one place and holds it
        time.sleep(1.2)
        c = self.tls(broker, timeout=12)                     # queued; starts when the silent peer's place is freed, and is not refused
        self.assertEqual(c.connect("late"), 0)
        silent.settimeout(2)
        self.assertEqual(silent.recv(16), b"")
        self.assertEqual(len(broker.events("refusal", "timeout.tls-handshake", wait=1)), 1)

    # ---- hostile input -------------------------------------------------------------------------------------------

    def test_garbage_before_a_client_hello(self):
        broker = self.start()
        for blob in (os.urandom(4096), b"GET / HTTP/1.1\r\nHost: x\r\n\r\n", b"\x16\x03\x01\xff\xff" + os.urandom(200),
                     b"\x16\x03\x01\x00\x10" + b"\x01\x00\x00\x0c", b"\x00" * 100):
            s = self.raw(broker)
            s.sendall(blob)
            s.settimeout(3)
            try:
                while s.recv(4096):
                    pass
            except (ConnectionResetError, socket.timeout):
                pass
        self.assertTrue(broker.events("refusal", "tls.failed", wait=3))
        self.assertTrue(broker.alive())
        self.tls(broker).connect("after")

    def test_a_connection_that_hangs_up_mid_handshake(self):
        broker = self.start()
        for _ in range(20):
            s = socket.create_connection(("127.0.0.1", broker.tls_port))
            s.sendall(b"\x16\x03\x01\x00\xc0" + os.urandom(40))
            s.close()
        time.sleep(0.5)
        self.assertTrue(broker.alive())
        self.tls(broker).connect("after")

    def test_a_flood_of_silent_connections_cannot_starve_the_table(self):
        broker = self.start("--tls-connections", "8", "--tls-handshake-timeout", "1")
        socks = [socket.create_connection(("127.0.0.1", broker.tls_port)) for _ in range(40)]
        for s in socks:
            self.addCleanup(s.close)
        time.sleep(2.5)                                       # their deadlines pass
        self.tls(broker, timeout=8).connect("survivor")

    def test_connect_and_disconnect_churn_is_flat_in_resident_size(self):
        broker = self.start("--tls-connections", "4")
        for _ in range(40):
            c = broker.tls_client()
            c.connect("warm")
            c.close()
        before = rss_kib(broker.proc.pid)
        for n in range(300):
            c = broker.tls_client()
            c.connect("churn")
            c.publish("t", b"x" * 100)
            c.close()
        time.sleep(0.5)
        after = rss_kib(broker.proc.pid)
        self.assertLess(after - before, 1024, "resident size grew %d KiB over 300 TLS connections" % (after - before))
        self.assertTrue(broker.alive())

    # ---- the identity --------------------------------------------------------------------------------------------

    def served_certificate(self, broker):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.minimum_version = ssl.TLSVersion.TLSv1_3
        with socket.create_connection(("127.0.0.1", broker.tls_port), timeout=3) as s:
            with ctx.wrap_socket(s) as t:
                return t.getpeercert(binary_form=True)

    def test_sighup_reads_the_identity_again_and_a_refused_one_keeps_the_old(self):
        broker = self.start()
        first = self.served_certificate(broker)
        self.addCleanup(make_identity)                         # leave a good identity for the tests after this one
        make_identity()
        self.assertEqual(self.served_certificate(broker), first)          # not until it is told
        broker.proc.send_signal(signal.SIGHUP)
        reloads = broker.events("reload", wait=3)
        self.assertEqual([r["ok"] for r in reloads], [True])
        second = self.served_certificate(broker)
        self.assertNotEqual(second, first)
        # A connection made before the reload goes on.
        before = self.tls(broker, context=tls_context())
        before.connect("old")
        (CERTS / "key.pem").write_text("not a key\n")
        broker.proc.send_signal(signal.SIGHUP)
        deadline = time.time() + 3
        while len(broker.events("reload")) < 2 and time.time() < deadline:
            time.sleep(0.05)
        reloads = broker.events("reload")
        self.assertEqual([r["ok"] for r in reloads], [True, False])
        self.assertNotIn("not a key", str(reloads))
        self.assertEqual(self.served_certificate(broker), second)         # the old identity keeps serving
        self.assertTrue(broker.alive())
        before.publish("t", b"still here")

    # ---- starting ------------------------------------------------------------------------------------------------

    def test_the_identity_files_are_read_at_start(self):
        key = CERTS / "key.pem"
        saved = key.read_bytes()
        self.addCleanup(key.write_bytes, saved)
        key.unlink()
        status, out, err = run("serve", "--port", "0", "--tls-port", str(harness.free_port()))
        self.assertEqual(status, 1)
        self.assertEqual(records(out)[0]["error"]["rule"], "tls.identity-unreadable")
        self.assertEqual(records(out)[0]["error"]["detail"]["what"], "key.pem")
        key.write_text("-----BEGIN PRIVATE KEY-----\nAAAA\n-----END PRIVATE KEY-----\n")
        status, out, err = run("serve", "--port", "0", "--tls-port", str(harness.free_port()))
        self.assertEqual(status, 2)
        error = records(out)[0]["error"]
        self.assertEqual(error["rule"], "tls.identity-refused")
        self.assertNotIn("AAAA", out)

    def test_a_key_that_is_not_the_leafs_is_refused(self):
        saved = (CERTS / "key.pem").read_bytes()
        self.addCleanup((CERTS / "key.pem").write_bytes, saved)
        import subprocess
        other = subprocess.run(["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256"],
                               capture_output=True, check=True).stdout
        (CERTS / "key.pem").write_bytes(other)
        status, out, err = run("serve", "--port", "0", "--tls-port", str(harness.free_port()))
        self.assertEqual(status, 2)
        self.assertEqual(records(out)[0]["error"]["rule"], "tls.identity-refused")

    def test_no_listener_at_all_is_a_conflict(self):
        status, out, err = run("serve", "--port", "0")
        self.assertEqual(status, 2)
        self.assertEqual(records(out)[0]["error"]["rule"], "args.conflict")

    def test_one_port_cannot_be_both(self):
        status, out, err = run("serve", "--port", "4000", "--tls-port", "4000")
        self.assertEqual(status, 2)
        self.assertEqual(records(out)[0]["error"]["rule"], "args.conflict")

    def test_a_tls_only_broker_has_no_plain_listener(self):
        broker = self.start(plain=False)
        listening = broker.events("listening")[0]
        self.assertEqual(listening["port"], 0)
        self.assertEqual(listening["tls_port"], broker.tls_port)
        self.tls(broker).connect("only")

    def test_without_tls_port_the_files_are_never_read(self):
        # A deployment without certificates starts as before: the directory is not touched.
        saved = {p.name: p.read_bytes() for p in CERTS.iterdir() if p.is_file()}
        self.addCleanup(lambda: [(CERTS / n).write_bytes(b) for n, b in saved.items()])
        for p in CERTS.iterdir():
            if p.name != harness.MARKER:
                p.unlink()
        broker = Broker()
        self.addCleanup(broker.__exit__, None, None, None)
        broker.client().connect("plain")


@unittest.skipUnless(HAVE_CLI, "mosquitto clients are not installed")
class MosquittoClients(unittest.TestCase):
    """The real command-line clients, with `--cafile`: what a deployment would run."""

    def test_pub_and_sub_over_tls(self):
        broker = Broker(tls=True, plain=False)
        self.addCleanup(broker.__exit__, None, None, None)
        cafile = str(CERTS / "chain.pem")
        sub = subprocess.Popen(["mosquitto_sub", "-p", str(broker.tls_port), "--cafile", cafile, "-t", "s/#", "-v", "-C", "1", "-q", "1"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(sub.kill)
        time.sleep(0.6)
        pub = subprocess.run(["mosquitto_pub", "-p", str(broker.tls_port), "--cafile", cafile, "-t", "s/t", "-m", "over tls", "-q", "1"],
                             capture_output=True, text=True, timeout=10)
        self.assertEqual(pub.returncode, 0, pub.stderr)
        out, err = sub.communicate(timeout=10)
        self.assertEqual(out.strip(), "s/t over tls", err)

    def test_a_client_that_does_not_trust_the_certificate_is_refused(self):
        broker = Broker(tls=True)
        self.addCleanup(broker.__exit__, None, None, None)
        pub = subprocess.run(["mosquitto_pub", "-p", str(broker.tls_port), "-t", "x", "-m", "y"], capture_output=True, text=True,
                             timeout=10)
        self.assertNotEqual(pub.returncode, 0)


@unittest.skipIf(paho is None, "paho-mqtt is not installed")
class PahoOverTls(unittest.TestCase):
    def test_publish_subscribe_qos_1(self):
        broker = Broker(tls=True)
        self.addCleanup(broker.__exit__, None, None, None)
        got, up = [], threading.Event()
        c = paho.Client(CallbackAPIVersion.VERSION2, client_id="paho-tls", protocol=paho.MQTTv311)
        c.tls_set(ca_certs=str(CERTS / "chain.pem"))
        c.on_connect = lambda cl, ud, flags, rc, props: up.set()
        c.on_message = lambda cl, ud, m: got.append((m.topic, bytes(m.payload)))
        c.connect("localhost", broker.tls_port, keepalive=30)
        c.loop_start()
        self.addCleanup(c.loop_stop)
        self.addCleanup(c.disconnect)
        self.assertTrue(up.wait(5), "paho did not connect over TLS")
        c.subscribe("p/#", qos=1)
        time.sleep(0.3)
        c.publish("p/1", b"one", qos=1).wait_for_publish(5)
        end = time.time() + 5
        while not got and time.time() < end:
            time.sleep(0.02)
        self.assertEqual(got, [("p/1", b"one")])


if __name__ == "__main__":
    unittest.main()
