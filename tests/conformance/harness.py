"""What the conformance tests share: the broker as a process, and a raw MQTT 3.1.1
client that writes the packets by hand, so a test can say exactly what the bytes
are (including wrong ones) and does not depend on a client library's manners.

The broker under test is `build/mqtt`, or $MQTT. Every test starts its own broker
on a free port, reads the NDJSON log it writes, and stops it with SIGTERM.
"""

import json
import os
import pathlib
import signal
import socket
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
BINARY = os.environ.get("MQTT", str(ROOT / "build" / "mqtt"))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Broker:
    """`mqtt serve` as a child process; its standard output is the log."""

    def __init__(self, *flags, port=None):
        self.port = port or free_port()
        self.flags = list(flags)
        self.proc = subprocess.Popen([BINARY, "serve", "--port", str(self.port), *self.flags],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.lines = []
        self._buf = b""
        os.set_blocking(self.proc.stdout.fileno(), False)
        deadline = time.time() + 10
        while time.time() < deadline:
            self.drain()
            if any(r.get("type") == "listening" for r in self.lines):
                return
            if self.proc.poll() is not None:
                break
            time.sleep(0.02)
        raise RuntimeError("the broker did not start: %r %r" % (self.lines, self.proc.stderr.read()))

    def drain(self):
        """Read what the broker has logged so far."""
        try:
            data = os.read(self.proc.stdout.fileno(), 1 << 20)
        except BlockingIOError:
            return
        self._buf += data
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            self.lines.append(json.loads(line))

    def events(self, kind=None, rule=None, wait=0.0):
        """Log records of `kind` (and `rule` for refusals), waiting up to `wait`
        seconds for at least one. The broker writes after each round of I/O, so a
        refusal may appear a moment after the client saw the close."""
        end = time.time() + wait
        while True:
            self.drain()
            found = [r for r in self.lines if (kind is None or r.get("type") == kind)
                     and (rule is None or r.get("rule") == rule)]
            if found or time.time() >= end:
                return found
            time.sleep(0.02)

    def alive(self):
        return self.proc.poll() is None

    def client(self, **kw):
        return Client(self.port, **kw)

    def stop(self):
        """SIGTERM, then the whole log. Answers (exit status, records)."""
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        os.set_blocking(self.proc.stdout.fileno(), True)
        rest = self.proc.stdout.read()
        self._buf += rest
        for line in self._buf.split(b"\n"):
            if line:
                self.lines.append(json.loads(line))
        self._buf = b""
        self.proc.stdout.close()
        self.proc.stderr.close()
        return self.proc.returncode, self.lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        for f in (self.proc.stdout, self.proc.stderr):
            try:
                f.close()
            except Exception:
                pass


def run(*args, timeout=20):
    """`mqtt <args>` to completion: (exit status, standard output as text, standard error)."""
    out = subprocess.run([BINARY, *args], capture_output=True, text=True, timeout=timeout)
    return out.returncode, out.stdout, out.stderr


def records(text):
    return [json.loads(line) for line in text.splitlines() if line]


# ---- packets, by hand ------------------------------------------------------

def varint(n):
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        out.append(b | (128 if n else 0))
        if not n:
            return bytes(out)


def pkt(first, body=b""):
    return bytes([first]) + varint(len(body)) + body


def s16(b):
    if isinstance(b, str):
        b = b.encode()
    return len(b).to_bytes(2, "big") + b


def connect_packet(client_id="c", clean=True, keepalive=60, will=None, username=None, password=None,
                   level=4, name=b"MQTT", flags_extra=0):
    flags = (2 if clean else 0) | flags_extra
    body = s16(name) + bytes([level])
    payload = s16(client_id)
    if will:
        topic, message, qos, retain = will
        flags |= 4 | (qos << 3) | (32 if retain else 0)
        payload += s16(topic) + s16(message)
    if username is not None:
        flags |= 128
        payload += s16(username)
    if password is not None:
        flags |= 64
        payload += s16(password)
    return pkt(0x10, body + bytes([flags]) + keepalive.to_bytes(2, "big") + payload)


def publish_packet(topic, payload=b"", qos=0, retain=False, dup=False, pid=1):
    if isinstance(payload, str):
        payload = payload.encode()
    first = 0x30 | (qos << 1) | (1 if retain else 0) | (8 if dup else 0)
    body = s16(topic) + (pid.to_bytes(2, "big") if qos else b"") + payload
    return pkt(first, body)


def subscribe_packet(filters, pid=1):
    body = pid.to_bytes(2, "big")
    for f, q in filters:
        body += s16(f) + bytes([q])
    return pkt(0x82, body)


def unsubscribe_packet(filters, pid=1):
    return pkt(0xA2, pid.to_bytes(2, "big") + b"".join(s16(f) for f in filters))


def puback_packet(pid):
    return pkt(0x40, pid.to_bytes(2, "big"))


def pubrec_packet(pid):
    return pkt(0x50, pid.to_bytes(2, "big"))


def pubrel_packet(pid):
    return pkt(0x62, pid.to_bytes(2, "big"))


def pubcomp_packet(pid):
    return pkt(0x70, pid.to_bytes(2, "big"))


PINGREQ = b"\xc0\x00"
DISCONNECT = b"\xe0\x00"


class Closed(Exception):
    pass


class Client:
    """A raw client. `recv()` answers (type, flags, body) of the next packet, raises
    `Closed` if the broker closed the connection, `TimeoutError` if nothing came."""

    def __init__(self, port, timeout=3.0):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.buf = b""
        self.timeout = timeout

    def send(self, data):
        self.sock.sendall(data)

    def _fill(self, n, timeout):
        self.sock.settimeout(timeout)
        while len(self.buf) < n:
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                raise TimeoutError("nothing arrived")
            except (ConnectionResetError, BrokenPipeError):
                raise Closed()
            if not chunk:
                raise Closed()
            self.buf += chunk

    def recv(self, timeout=None):
        timeout = self.timeout if timeout is None else timeout
        self._fill(2, timeout)
        i = 1
        n = 0
        mult = 1
        while True:
            self._fill(i + 1, timeout)
            b = self.buf[i]
            n += (b & 127) * mult
            mult *= 128
            i += 1
            if not b & 128:
                break
        self._fill(i + n, timeout)
        first = self.buf[0]
        body = self.buf[i:i + n]
        self.buf = self.buf[i + n:]
        return first >> 4, first & 15, body

    def expect(self, kind, timeout=None):
        k, flags, body = self.recv(timeout)
        assert k == kind, "expected packet type %d, got %d (%r)" % (kind, k, body)
        return flags, body

    def connect(self, client_id="c", expect_code=0, **kw):
        self.send(connect_packet(client_id, **kw))
        _, body = self.expect(2)
        assert body[1] == expect_code, "CONNACK code %d, wanted %d" % (body[1], expect_code)
        return body[0] & 1

    def subscribe(self, filters, pid=1):
        if isinstance(filters, str):
            filters = [(filters, 0)]
        self.send(subscribe_packet(filters, pid))
        _, body = self.expect(9)
        assert int.from_bytes(body[:2], "big") == pid
        return list(body[2:])

    def publish(self, topic, payload=b"", qos=0, retain=False, pid=1):
        self.send(publish_packet(topic, payload, qos, retain, pid=pid))
        if qos == 1:
            _, body = self.expect(4)
            assert int.from_bytes(body, "big") == pid

    def publish2(self, topic, payload=b"", retain=False, pid=1):
        """A complete QoS 2 publish from this client: PUBLISH, PUBREC, PUBREL, PUBCOMP."""
        self.send(publish_packet(topic, payload, qos=2, retain=retain, pid=pid))
        _, body = self.expect(5)
        assert int.from_bytes(body, "big") == pid
        self.send(pubrel_packet(pid))
        _, body = self.expect(7)
        assert int.from_bytes(body, "big") == pid

    def recv_publish(self, timeout=None):
        """(topic, payload, qos, retain, dup, pid) of the next PUBLISH."""
        k, flags, body = self.recv(timeout)
        assert k == 3, "expected PUBLISH, got type %d" % k
        tl = int.from_bytes(body[:2], "big")
        topic = body[2:2 + tl].decode()
        qos = (flags >> 1) & 3
        at = 2 + tl
        pid = 0
        if qos:
            pid = int.from_bytes(body[at:at + 2], "big")
            at += 2
        return topic, body[at:], qos, bool(flags & 1), bool(flags & 8), pid

    def closed(self, timeout=2.0):
        """True if the broker closes the connection within `timeout` seconds (any
        packets still in flight are read and discarded first)."""
        end = time.time() + timeout
        while time.time() < end:
            try:
                self.recv(max(0.05, end - time.time()))
            except Closed:
                return True
            except TimeoutError:
                continue
        return False

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
