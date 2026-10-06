"""A raw MQTT 3.1.1 client, enough for the latency probe and the readiness check.

No third-party code: the probe is the same for every broker, and the packets are
written out here so the measurement does not depend on a client library's
scheduling.
"""

import socket
import time


def _str(b):
    return len(b).to_bytes(2, "big") + b


def _remaining(n):
    out = bytearray()
    while True:
        byte = n % 128
        n //= 128
        out.append(byte | (128 if n else 0))
        if not n:
            return bytes(out)


def packet(first, body):
    return bytes([first]) + _remaining(len(body)) + body


def connect(host, port, client_id, keepalive=60, timeout=30.0):
    s = socket.create_connection((host, port), timeout=timeout)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    body = _str(b"MQTT") + bytes([4, 2]) + keepalive.to_bytes(2, "big") + _str(client_id.encode())
    s.sendall(packet(0x10, body))
    ack = recv_exact(s, 4)
    if ack[0] != 0x20 or ack[3] != 0:
        raise RuntimeError("CONNACK %r" % ack)
    return s


def recv_exact(s, n):
    data = bytearray()
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return bytes(data)


def subscribe(s, topic, qos=0, pid=1):
    s.sendall(packet(0x82, pid.to_bytes(2, "big") + _str(topic.encode()) + bytes([qos])))
    ack = recv_exact(s, 5)
    if ack[0] != 0x90 or ack[4] > 2:
        raise RuntimeError("SUBACK %r" % ack)


def publish_qos0(topic, payload):
    return packet(0x30, _str(topic.encode()) + payload)


def read_publish(s):
    first = recv_exact(s, 1)[0]
    n, shift = 0, 0
    while True:
        b = recv_exact(s, 1)[0]
        n |= (b & 127) << shift
        shift += 7
        if not b & 128:
            break
    recv_exact(s, n)
    return first


def ready(host, port, deadline_s=120):
    end = time.time() + deadline_s
    while time.time() < end:
        try:
            connect(host, port, "ready-probe").close()
            return True
        except Exception:
            time.sleep(0.5)
    return False


def latency(host, port, samples, warmup, size=64):
    """Closed loop, one message in flight: publish, wait for it to arrive at a
    second connection, in nanoseconds. Returns the sorted samples."""
    sub = connect(host, port, "lat-sub")
    pub = connect(host, port, "lat-pub")
    subscribe(sub, "lat/probe")
    msg = publish_qos0("lat/probe", b"x" * size)
    out = []
    for i in range(samples + warmup):
        t0 = time.perf_counter_ns()
        pub.sendall(msg)
        read_publish(sub)
        t1 = time.perf_counter_ns()
        if i >= warmup:
            out.append(t1 - t0)
    sub.close()
    pub.close()
    out.sort()
    return out
