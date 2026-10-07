edition 5;

import std.test;
import mqtt.wire;

// A packet written byte by byte so the tests do not depend on the encoder.
fn put[&b](buf: &!b [byte], at: int, v: int) -> [] int {
    buf[at] = byte_of(v);
    return at + 1;
}

fn str[&b, &s](buf: &!b [byte], at: int, text: &s [byte]) -> [] int {
    buf[at] = byte_of(len(text) / 256);
    buf[at + 1] = byte_of(len(text) % 256);
    var i = 0;
    while i < len(text) {
        buf[at + 2 + i] = text[i];
        i = i + 1;
    }
    return at + 2 + len(text);
}

pub fn test_header_lengths() -> [] int {
    region a {
        let b = alloc_slice[a](16, byte_of(0));
        // PINGREQ: type 12, length 0.
        b[0] = byte_of(192);
        b[1] = byte_of(0);
        test.assert_eq(wire.header(b[0..2], 100), 2);
        test.assert_eq(wire.kind(b[0..2]), 12);
        // One byte is not enough.
        test.assert_eq(wire.header(b[0..1], 100), wire.incomplete());
        test.assert_eq(wire.header(b[0..0], 100), wire.incomplete());
        // 127 and 128, the edge of one length byte.
        b[0] = byte_of(48);
        b[1] = byte_of(127);
        test.assert_eq(wire.header(b[0..2], 1000), 129);
        b[1] = byte_of(128);
        test.assert_eq(wire.header(b[0..2], 1000), wire.incomplete());
        b[2] = byte_of(1);
        test.assert_eq(wire.header(b[0..3], 1000), 3 + 128);
        test.assert_eq(wire.header_size(b[0..3]), 3);
        // The same value encoded with a zero last byte is not minimal.
        b[1] = byte_of(128);
        b[2] = byte_of(0);
        test.assert_eq(wire.header(b[0..3], 1000), wire.bad_length());
        // Five length bytes.
        b[1] = byte_of(255);
        b[2] = byte_of(255);
        b[3] = byte_of(255);
        b[4] = byte_of(255);
        b[5] = byte_of(1);
        test.assert_eq(wire.header(b[0..6], 1000), wire.bad_length());
        // The largest legal length is refused by a small bound, not trapped on.
        b[1] = byte_of(255);
        b[2] = byte_of(255);
        b[3] = byte_of(255);
        b[4] = byte_of(127);
        test.assert_eq(wire.header(b[0..5], 65536), wire.too_large());
        // Exactly the bound is accepted; one over is not.
        b[1] = byte_of(255);
        b[2] = byte_of(255);
        b[3] = byte_of(3);
        test.assert_eq(wire.header(b[0..4], 65535 + 4), 65535 + 4);
        test.assert_eq(wire.header(b[0..4], 65535 + 3), wire.too_large());
    }
    return 0;
}

pub fn test_check_kind() -> [] int {
    test.assert_eq(wire.check_kind(1, 0), 0);
    test.assert_eq(wire.check_kind(1, 1), wire.e_reserved());
    test.assert_eq(wire.check_kind(3, 15), 0);
    test.assert_eq(wire.check_kind(8, 2), 0);
    test.assert_eq(wire.check_kind(8, 0), wire.e_reserved());
    test.assert_eq(wire.check_kind(10, 3), wire.e_reserved());
    test.assert_eq(wire.check_kind(6, 2), 0);
    test.assert_eq(wire.check_kind(6, 0), wire.e_reserved());
    test.assert_eq(wire.check_kind(5, 0), 0);
    test.assert_eq(wire.check_kind(5, 2), wire.e_reserved());
    test.assert_eq(wire.check_kind(7, 0), 0);
    test.assert_eq(wire.check_kind(7, 1), wire.e_reserved());
    test.assert_eq(wire.check_kind(2, 0), wire.e_unexpected());
    test.assert_eq(wire.check_kind(0, 0), wire.e_malformed());
    test.assert_eq(wire.check_kind(15, 0), wire.e_malformed());
    return 0;
}

// CONNECT, clean session, keepalive 60, client id "abc".
fn connect_packet[&b](buf: &!b [byte], flags: int) -> [] int {
    var p = 0;
    p = put(buf, p, 16);
    p = put(buf, p, 0);
    p = str(buf, p, "MQTT");
    p = put(buf, p, 4);
    p = put(buf, p, flags);
    p = put(buf, p, 0);
    p = put(buf, p, 60);
    p = str(buf, p, "abc");
    buf[1] = byte_of(p - 2);
    return p;
}

pub fn test_connect() -> [] int {
    region a {
        let b = alloc_slice[a](64, byte_of(0));
        let t = alloc_slice[a](wire.connect_slots(), 0);
        var n = connect_packet(b, 2);
        test.assert_eq(wire.header(b[0..n], 100), n);
        test.assert_eq(wire.decode_connect(b[0..n], t), 0);
        test.assert_eq(t[wire.c_level()], 4);
        test.assert_eq(t[wire.c_flags()], 2);
        test.assert_eq(t[wire.c_keepalive()], 60);
        test.assert_eq(t[wire.c_id() + 1], 3);
        test.assert_eq(int_of(b[t[wire.c_id()]]), 97);
        // Every shorter prefix of the same packet is refused, never trapped on.
        var cut = 2;
        while cut < n {
            let r = wire.decode_connect(b[0..cut], t);
            test.assert(r < 0);
            cut = cut + 1;
        }
        // The reserved flag bit.
        n = connect_packet(b, 3);
        test.assert_eq(wire.decode_connect(b[0..n], t), wire.e_reserved());
        // Will QoS without a will; QoS 3.
        n = connect_packet(b, 2 + 8);
        test.assert_eq(wire.decode_connect(b[0..n], t), wire.e_malformed());
        n = connect_packet(b, 2 + 4 + 24);
        test.assert_eq(wire.decode_connect(b[0..n], t), wire.e_qos3());
        // A password without a user name.
        n = connect_packet(b, 2 + 64);
        test.assert_eq(wire.decode_connect(b[0..n], t), wire.e_malformed());
        // A wrong protocol name: "MQTX".
        n = connect_packet(b, 2);
        b[7] = byte_of(88);
        test.assert_eq(wire.decode_connect(b[0..n], t), wire.e_name());
        // Trailing bytes after the client id.
        n = connect_packet(b, 2);
        b[n] = byte_of(0);
        b[1] = byte_of(n - 1);
        test.assert_eq(wire.decode_connect(b[0..n + 1], t), wire.e_malformed());
        // A NUL in the client id.
        n = connect_packet(b, 2);
        b[n - 1] = byte_of(0);
        test.assert_eq(wire.decode_connect(b[0..n], t), wire.e_client_id());
    }
    return 0;
}

pub fn test_connect_with_will_and_credentials() -> [] int {
    region a {
        let b = alloc_slice[a](96, byte_of(0));
        let t = alloc_slice[a](wire.connect_slots(), 0);
        var p = 0;
        p = put(b, p, 16);
        p = put(b, p, 0);
        p = str(b, p, "MQTT");
        p = put(b, p, 4);
        // user, password, will retain 0, will qos 1, will, clean.
        p = put(b, p, 128 + 64 + 8 + 4 + 2);
        p = put(b, p, 0);
        p = put(b, p, 30);
        p = str(b, p, "id");
        p = str(b, p, "will/topic");
        p = str(b, p, "bye");
        p = str(b, p, "user");
        p = str(b, p, "pw");
        b[1] = byte_of(p - 2);
        test.assert_eq(wire.decode_connect(b[0..p], t), 0);
        test.assert_eq(t[wire.c_will_topic() + 1], 10);
        test.assert_eq(t[wire.c_will_message() + 1], 3);
        test.assert_eq(t[wire.c_user() + 1], 4);
        test.assert_eq(t[wire.c_pass() + 1], 2);
        test.assert_eq(int_of(b[t[wire.c_will_message()]]), 98);
    }
    return 0;
}

pub fn test_publish() -> [] int {
    region a {
        let b = alloc_slice[a](64, byte_of(0));
        let t = alloc_slice[a](wire.publish_slots(), 0);
        // QoS 1, retain, topic "a/b", id 7, payload "hi".
        var p = 0;
        p = put(b, p, 48 + 2 + 1);
        p = put(b, p, 0);
        p = str(b, p, "a/b");
        p = put(b, p, 0);
        p = put(b, p, 7);
        p = put(b, p, 104);
        p = put(b, p, 105);
        b[1] = byte_of(p - 2);
        test.assert_eq(wire.decode_publish(b[0..p], t), 0);
        test.assert_eq(t[wire.p_qos()], 1);
        test.assert_eq(t[wire.p_retain()], 1);
        test.assert_eq(t[wire.p_pid()], 7);
        test.assert_eq(t[wire.p_topic() + 1], 3);
        test.assert_eq(t[wire.p_payload() + 1], 2);
        test.assert_eq(int_of(b[t[wire.p_payload()]]), 104);
        // QoS 3.
        b[0] = byte_of(48 + 6);
        test.assert_eq(wire.decode_publish(b[0..p], t), wire.e_qos3());
        // DUP on QoS 0.
        b[0] = byte_of(48 + 8);
        test.assert_eq(wire.decode_publish(b[0..p], t), wire.e_malformed());
        // Packet id zero.
        b[0] = byte_of(48 + 2);
        b[7] = byte_of(0);
        b[8] = byte_of(0);
        test.assert_eq(wire.decode_publish(b[0..p], t), wire.e_packet_id());
        // Every prefix.
        var cut = 2;
        while cut < p {
            test.assert(wire.decode_publish(b[0..cut], t) <= 0);
            cut = cut + 1;
        }
        // Empty topic.
        var q = 0;
        q = put(b, q, 48);
        q = put(b, q, 2);
        q = str(b, q, "");
        test.assert_eq(wire.decode_publish(b[0..q], t), wire.e_topic());
        // Empty payload is fine.
        q = 0;
        q = put(b, q, 48);
        q = put(b, q, 3);
        q = str(b, q, "x");
        test.assert_eq(wire.decode_publish(b[0..q], t), 0);
        test.assert_eq(t[wire.p_payload() + 1], 0);
    }
    return 0;
}

pub fn test_subscribe() -> [] int {
    region a {
        let b = alloc_slice[a](64, byte_of(0));
        let t = alloc_slice[a](wire.filter_slots(), 0);
        var p = 0;
        p = put(b, p, 130);
        p = put(b, p, 0);
        p = put(b, p, 0);
        p = put(b, p, 9);
        p = str(b, p, "a/+");
        p = put(b, p, 1);
        p = str(b, p, "b/#");
        p = put(b, p, 2);
        b[1] = byte_of(p - 2);
        test.assert_eq(wire.decode_filters(b[0..p], t, true), 0);
        test.assert_eq(t[0], 9);
        test.assert_eq(t[1], 2);
        test.assert_eq(t[3], 3);
        test.assert_eq(t[4], 1);
        test.assert_eq(t[6], 3);
        test.assert_eq(t[7], 2);
        // QoS byte above 2.
        b[p - 1] = byte_of(3);
        test.assert_eq(wire.decode_filters(b[0..p], t, true), wire.e_malformed());
        b[p - 1] = byte_of(2);
        // No filters.
        var q = 0;
        q = put(b, q, 130);
        q = put(b, q, 2);
        q = put(b, q, 0);
        q = put(b, q, 9);
        test.assert_eq(wire.decode_filters(b[0..q], t, true), wire.e_malformed());
        // Packet id zero.
        b[3] = byte_of(0);
        test.assert_eq(wire.decode_filters(b[0..p], t, true), wire.e_packet_id());
        b[3] = byte_of(9);
        // Missing QoS byte on the last filter.
        test.assert_eq(wire.decode_filters(b[0..p - 1], t, true), wire.e_malformed());
        // A prefix that ends between filters is a valid shorter packet: the decoder
        // trusts `header` for where a packet ends. No prefix may trap.
        var cut = 2;
        while cut < p {
            wire.decode_filters(b[0..cut], t, true);
            wire.decode_filters(b[0..cut], t, false);
            cut = cut + 1;
        }
    }
    return 0;
}

pub fn test_puback() -> [] int {
    region a {
        let b = alloc_slice[a](8, byte_of(0));
        b[0] = byte_of(64);
        b[1] = byte_of(2);
        b[2] = byte_of(1);
        b[3] = byte_of(2);
        test.assert_eq(wire.decode_puback(b[0..4]), 258);
        test.assert_eq(wire.decode_puback(b[0..3]), wire.e_malformed());
        b[2] = byte_of(0);
        b[3] = byte_of(0);
        test.assert_eq(wire.decode_puback(b[0..4]), wire.e_packet_id());
    }
    return 0;
}

pub fn test_encoders() -> [] int {
    region a {
        let b = alloc_slice[a](300, byte_of(0));
        var n = wire.put_connack(b, 0, 1, 0);
        test.assert_eq(n, 4);
        test.assert_eq(int_of(b[0]), 32);
        test.assert_eq(int_of(b[2]), 1);
        test.assert_eq(wire.put_connack(b[0..3], 0, 1, 0), 0 - 1);
        n = wire.put_puback(b, 0, 513);
        test.assert_eq(n, 4);
        test.assert_eq(int_of(b[2]), 2);
        test.assert_eq(int_of(b[3]), 1);
        n = wire.put_pubrec(b, 0, 513);
        test.assert_eq(n, 4);
        test.assert_eq(int_of(b[0]), 80);
        test.assert_eq(wire.decode_puback(b[0..4]), 513);
        n = wire.put_pubrel(b, 0, 513);
        test.assert_eq(int_of(b[0]), 98);
        test.assert_eq(wire.check_kind(int_of(b[0]) >> 4, int_of(b[0]) & 15), 0);
        test.assert_eq(wire.decode_puback(b[0..4]), 513);
        n = wire.put_pubcomp(b, 0, 513);
        test.assert_eq(int_of(b[0]), 112);
        test.assert_eq(wire.put_pubcomp(b[0..3], 0, 1), 0 - 1);
        n = wire.put_pingresp(b, 0);
        test.assert_eq(n, 2);
        test.assert_eq(int_of(b[0]), 208);
        n = wire.put_unsuback(b, 0, 5);
        test.assert_eq(int_of(b[0]), 176);
        let codes = alloc_slice[a](3, 0);
        codes[0] = 0;
        codes[1] = 1;
        codes[2] = 128;
        n = wire.put_suback(b, 0, 9, codes, 3);
        test.assert_eq(n, 7);
        test.assert_eq(int_of(b[1]), 5);
        test.assert_eq(int_of(b[6]), 128);
        test.assert_eq(wire.put_suback(b, 0, 9, codes, 4), 0 - 1);
        // PUBLISH round trip through the decoder.
        let t = alloc_slice[a](wire.publish_slots(), 0);
        n = wire.put_publish(b, 0, "t/x", "payload", 1, 1, 1, 77);
        test.assert_eq(n, wire.publish_size(3, 7, 1));
        test.assert_eq(wire.header(b[0..n], 1000), n);
        test.assert_eq(wire.decode_publish(b[0..n], t), 0);
        test.assert_eq(t[wire.p_qos()], 1);
        test.assert_eq(t[wire.p_dup()], 1);
        test.assert_eq(t[wire.p_retain()], 1);
        test.assert_eq(t[wire.p_pid()], 77);
        test.assert_eq(t[wire.p_payload() + 1], 7);
        // No room: refused, not trapped.
        test.assert_eq(wire.put_publish(b[0..n - 1], 0, "t/x", "payload", 1, 1, 1, 77), 0 - 1);
        // A payload past one length byte and past two.
        let big = alloc_slice[a](200, byte_of(65));
        let out = alloc_slice[a](300, byte_of(0));
        n = wire.put_publish(out, 0, "k", big, 0, 0, 0, 0);
        test.assert_eq(n, 1 + 2 + 2 + 1 + 200);
        test.assert_eq(wire.header(out[0..n], 1000), n);
    }
    return 0;
}

// A deterministic stream of bytes: no packet made of them may trap any decoder.
fn next(seed: int) -> [] int {
    return (seed * 1103515245 + 12345) % 2147483648;
}

pub fn test_no_input_reaches_a_trap() -> [] int {
    region a {
        let buf = alloc_slice[a](96, byte_of(0));
        let ct = alloc_slice[a](wire.connect_slots(), 0);
        let pt = alloc_slice[a](wire.publish_slots(), 0);
        let ft = alloc_slice[a](wire.filter_slots(), 0);
        var seed = 12345;
        var round = 0;
        while round < 4000 {
            var i = 0;
            while i < len(buf) {
                seed = next(seed);
                // Bias towards small values so lengths are often plausible.
                if seed / 65536 % 3 == 0 {
                    buf[i] = byte_of(seed / 256 % 256);
                } else {
                    buf[i] = byte_of(seed / 256 % 24);
                }
                i = i + 1;
            }
            seed = next(seed);
            let n = 2 + seed / 256 % (len(buf) - 1);
            let view = buf[0..n];
            let total = wire.header(view, 70000);
            if total > 0 && total <= n {
                let whole = buf[0..total];
                let k = wire.kind(whole);
                wire.check_kind(k, wire.flags(whole));
                wire.decode_connect(whole, ct);
                wire.decode_publish(whole, pt);
                wire.decode_filters(whole, ft, true);
                wire.decode_filters(whole, ft, false);
                wire.decode_puback(whole);
                wire.decode_empty(whole);
            }
            round = round + 1;
        }
    }
    return 0;
}
