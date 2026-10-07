edition 5;

// `mqtt.wire` -- the MQTT 3.1.1 packet codec (design section 1, issue #3).
//
// Everything here is total: no input reaches a trap. Every read is preceded by a
// bounds check that answers a negative code instead, and every code is a rule of
// design section 5 (`rules.tag_of` turns it into the tag the logs and counters
// use). Decoders fill an `int` table the caller owns, with offsets into the
// packet, so no byte is copied to be parsed; encoders write into a caller's
// slice at an offset and answer the new offset, or -1 when it does not fit.
//
// The fixed header is parsed by `header` alone, which is what lets a connection
// refuse a packet whose declared length is over the bound *before* buffering it.

module mqtt.wire;

import std.utf8;

// ---- codes ---------------------------------------------------------------

// `header`: not enough bytes yet.
pub fn incomplete() -> [] int {
    return 0 - 1;
}

// `header`: a remaining length that is not a valid one (more than four bytes,
// or a longer encoding than the value needs).
pub fn bad_length() -> [] int {
    return 0 - 2;
}

// `header`: the packet is longer than the bound.
pub fn too_large() -> [] int {
    return 0 - 3;
}

// Decoders: 0 is success; these are the refusals.
pub fn e_malformed() -> [] int {
    return 0 - 10;
}

pub fn e_reserved() -> [] int {
    return 0 - 11;
}

pub fn e_name() -> [] int {
    return 0 - 12;
}

pub fn e_level() -> [] int {
    return 0 - 13;
}

pub fn e_client_id() -> [] int {
    return 0 - 14;
}

pub fn e_qos3() -> [] int {
    return 0 - 15;
}

pub fn e_topic() -> [] int {
    return 0 - 16;
}

pub fn e_packet_id() -> [] int {
    return 0 - 17;
}

pub fn e_unexpected() -> [] int {
    return 0 - 18;
}

// ---- packet types --------------------------------------------------------

pub fn t_connect() -> [] int {
    return 1;
}

pub fn t_connack() -> [] int {
    return 2;
}

pub fn t_publish() -> [] int {
    return 3;
}

pub fn t_puback() -> [] int {
    return 4;
}

pub fn t_pubrec() -> [] int {
    return 5;
}

pub fn t_pubrel() -> [] int {
    return 6;
}

pub fn t_pubcomp() -> [] int {
    return 7;
}

pub fn t_subscribe() -> [] int {
    return 8;
}

pub fn t_suback() -> [] int {
    return 9;
}

pub fn t_unsubscribe() -> [] int {
    return 10;
}

pub fn t_unsuback() -> [] int {
    return 11;
}

pub fn t_pingreq() -> [] int {
    return 12;
}

pub fn t_pingresp() -> [] int {
    return 13;
}

pub fn t_disconnect() -> [] int {
    return 14;
}

// How many filters one SUBSCRIBE or UNSUBSCRIBE may carry. A packet with more is
// refused as malformed: the tables are sized for this.
pub fn max_filters() -> [] int {
    return 256;
}

// Longest string any packet field may hold (a 16-bit length).
pub fn string_limit() -> [] int {
    return 65535;
}

// ---- the fixed header ----------------------------------------------------

// The fixed header of the packet at the front of `data`.
//
// Answers `total`, the whole packet's length in bytes (header included), or one
// of `incomplete`, `bad_length`, `too_large` (`max` is the largest packet that
// is accepted). Call `kind` and `flags` for the first byte's halves and
// `header_size` for where the body starts.
pub fn header[&d](data: &d [byte], max: int) -> [] int {
    if len(data) < 2 {
        return incomplete();
    }
    var value = 0;
    var mult = 1;
    var i = 1;
    var going = true;
    var last = 0;
    while going {
        if i >= len(data) {
            return incomplete();
        }
        let b = int_of(data[i]);
        value = value + (b & 127) * mult;
        last = b;
        i = i + 1;
        if b & 128 == 0 {
            going = false;
        } else if i >= 5 {
            return bad_length();
        } else {
            mult = mult * 128;
        }
    }
    // The shortest encoding: a multi-byte length may not end in a zero byte.
    if i > 2 && last == 0 {
        return bad_length();
    }
    let total = i + value;
    if total > max {
        return too_large();
    }
    return total;
}

// Bytes before the body: one for the type and flags, then the remaining length's.
// Only meaningful after `header` answered a total.
pub fn header_size[&d](data: &d [byte]) -> [] int {
    var i = 1;
    while i < len(data) && i < 5 && int_of(data[i]) & 128 != 0 {
        i = i + 1;
    }
    return i + 1;
}

pub fn kind[&d](data: &d [byte]) -> [] int {
    if len(data) < 1 {
        return 0;
    }
    return int_of(data[0]) >> 4;
}

pub fn flags[&d](data: &d [byte]) -> [] int {
    if len(data) < 1 {
        return 0;
    }
    return int_of(data[0]) & 15;
}

// What a client may send, by type: 0 if it is allowed, else the refusal. The
// flags a type requires are checked here too (3.x.1: reserved bits).
pub fn check_kind(k: int, f: int) -> [] int {
    if k == 1 || k == 12 || k == 14 || k == 4 || k == 5 || k == 7 {
        if f != 0 {
            return e_reserved();
        }
        return 0;
    }
    if k == 3 {
        return 0;
    }
    if k == 8 || k == 10 || k == 6 {
        if f != 2 {
            return e_reserved();
        }
        return 0;
    }
    if k == 2 || k == 9 || k == 11 || k == 13 {
        return e_unexpected();
    }
    // Types 0 and 15 are reserved.
    return e_malformed();
}

// ---- reading fields ------------------------------------------------------

// The big-endian 16-bit value at `at`, or -1 if there are not two bytes.
fn be16[&d](data: &d [byte], at: int, end: int) -> [] int {
    if at < 0 || at + 2 > end {
        return 0 - 1;
    }
    return int_of(data[at]) << 8 | int_of(data[at + 1]);
}

// Is `text` valid MQTT UTF-8: well formed, and with no U+0000 (1.5.3).
fn clean[&d](data: &d [byte], at: int, n: int) -> [] bool {
    var i = at;
    while i < at + n {
        if int_of(data[i]) == 0 {
            return false;
        }
        i = i + 1;
    }
    return utf8.is_valid(data[at..at + n]);
}

// ---- CONNECT -------------------------------------------------------------

// Table slots of a decoded CONNECT; every offset is into the packet.
pub fn c_level() -> [] int {
    return 0;
}

pub fn c_flags() -> [] int {
    return 1;
}

pub fn c_keepalive() -> [] int {
    return 2;
}

pub fn c_id() -> [] int {
    return 3;
}

pub fn c_will_topic() -> [] int {
    return 5;
}

pub fn c_will_message() -> [] int {
    return 7;
}

pub fn c_user() -> [] int {
    return 9;
}

pub fn c_pass() -> [] int {
    return 11;
}

pub fn connect_slots() -> [] int {
    return 13;
}

// Decode a CONNECT whose whole packet is `data` into `t` (`connect_slots` slots).
// 0 on success, or the refusal. The protocol *level* is stored and checked by the
// caller, because an unsupported level is answered with a CONNACK, not a close;
// everything that must close the connection is refused here.
pub fn decode_connect[&d, &t](data: &d [byte], t: &!t [int]) -> [] int {
    let end = len(data);
    var p = header_size(data);
    let nlen = be16(data, p, end);
    if nlen < 0 {
        return e_malformed();
    }
    p = p + 2;
    if p + nlen > end {
        return e_malformed();
    }
    if nlen != 4 || int_of(data[p]) != 77 || int_of(data[p + 1]) != 81 || int_of(data[p + 2]) != 84 || int_of(data[p + 3]) != 84 {
        return e_name();
    }
    p = p + nlen;
    if p + 4 > end {
        return e_malformed();
    }
    let level = int_of(data[p]);
    let fl = int_of(data[p + 1]);
    let keep = be16(data, p + 2, end);
    p = p + 4;
    t[0] = level;
    t[1] = fl;
    t[2] = keep;
    var i = 3;
    while i < 13 {
        t[i] = 0;
        i = i + 1;
    }
    // [MQTT-3.1.2-3] the reserved bit is zero.
    if fl & 1 != 0 {
        return e_reserved();
    }
    let will = fl >> 2 & 1;
    let wqos = fl >> 3 & 3;
    let wretain = fl >> 5 & 1;
    let pass = fl >> 6 & 1;
    let user = fl >> 7 & 1;
    // [MQTT-3.1.2-11, 13, 14, 22].
    if will == 0 && (wqos != 0 || wretain != 0) {
        return e_malformed();
    }
    if wqos == 3 {
        return e_qos3();
    }
    if user == 0 && pass == 1 {
        return e_malformed();
    }
    // Client identifier.
    let idlen = be16(data, p, end);
    if idlen < 0 {
        return e_malformed();
    }
    p = p + 2;
    if p + idlen > end {
        return e_malformed();
    }
    if !clean(data, p, idlen) {
        return e_client_id();
    }
    t[3] = p;
    t[4] = idlen;
    p = p + idlen;
    if will == 1 {
        let tl = be16(data, p, end);
        if tl < 0 {
            return e_malformed();
        }
        p = p + 2;
        if p + tl > end {
            return e_malformed();
        }
        if !clean(data, p, tl) || tl == 0 {
            return e_topic();
        }
        t[5] = p;
        t[6] = tl;
        p = p + tl;
        let ml = be16(data, p, end);
        if ml < 0 {
            return e_malformed();
        }
        p = p + 2;
        if p + ml > end {
            return e_malformed();
        }
        t[7] = p;
        t[8] = ml;
        p = p + ml;
    }
    if user == 1 {
        let ul = be16(data, p, end);
        if ul < 0 {
            return e_malformed();
        }
        p = p + 2;
        if p + ul > end {
            return e_malformed();
        }
        if !clean(data, p, ul) {
            return e_malformed();
        }
        t[9] = p;
        t[10] = ul;
        p = p + ul;
    }
    if pass == 1 {
        let pl = be16(data, p, end);
        if pl < 0 {
            return e_malformed();
        }
        p = p + 2;
        if p + pl > end {
            return e_malformed();
        }
        t[11] = p;
        t[12] = pl;
        p = p + pl;
    }
    if p != end {
        return e_malformed();
    }
    return 0;
}

// ---- PUBLISH -------------------------------------------------------------

pub fn p_topic() -> [] int {
    return 0;
}

pub fn p_pid() -> [] int {
    return 2;
}

pub fn p_payload() -> [] int {
    return 3;
}

pub fn p_qos() -> [] int {
    return 5;
}

pub fn p_retain() -> [] int {
    return 6;
}

pub fn p_dup() -> [] int {
    return 7;
}

pub fn publish_slots() -> [] int {
    return 8;
}

// Decode a PUBLISH into `t` (`publish_slots` slots). The topic's *wildcard* rules
// are `topic.valid_name`'s; here the bytes are only required to be clean UTF-8.
pub fn decode_publish[&d, &t](data: &d [byte], t: &!t [int]) -> [] int {
    let end = len(data);
    let fl = flags(data);
    let qos = fl >> 1 & 3;
    let dup = fl >> 3 & 1;
    let retain = fl & 1;
    t[5] = qos;
    t[6] = retain;
    t[7] = dup;
    if qos == 3 {
        return e_qos3();
    }
    // [MQTT-3.3.1-2] DUP is zero on a QoS 0 message.
    if qos == 0 && dup == 1 {
        return e_malformed();
    }
    var p = header_size(data);
    let tl = be16(data, p, end);
    if tl < 0 {
        return e_malformed();
    }
    p = p + 2;
    if p + tl > end {
        return e_malformed();
    }
    if tl == 0 || !clean(data, p, tl) {
        return e_topic();
    }
    t[0] = p;
    t[1] = tl;
    p = p + tl;
    t[2] = 0;
    if qos > 0 {
        let pid = be16(data, p, end);
        if pid < 0 {
            return e_malformed();
        }
        if pid == 0 {
            return e_packet_id();
        }
        t[2] = pid;
        p = p + 2;
    }
    t[3] = p;
    t[4] = end - p;
    return 0;
}

// ---- SUBSCRIBE and UNSUBSCRIBE -------------------------------------------

// `t[0]` is the packet id, `t[1]` how many filters, then `t[2 + 3k..]` is
// `offset, length, qos` of filter k (qos 0 for an UNSUBSCRIBE).
pub fn filter_slots() -> [] int {
    return 2 + 3 * max_filters();
}

// Decode a SUBSCRIBE (`with_qos`) or UNSUBSCRIBE (`filter_slots` slots).
pub fn decode_filters[&d, &t](data: &d [byte], t: &!t [int], with_qos: bool) -> [] int {
    let end = len(data);
    var p = header_size(data);
    let pid = be16(data, p, end);
    if pid < 0 {
        return e_malformed();
    }
    if pid == 0 {
        return e_packet_id();
    }
    p = p + 2;
    t[0] = pid;
    t[1] = 0;
    var n = 0;
    // [MQTT-3.8.3-3] at least one filter.
    if p >= end {
        return e_malformed();
    }
    while p < end {
        if n >= max_filters() {
            return e_malformed();
        }
        let fl = be16(data, p, end);
        if fl < 0 {
            return e_malformed();
        }
        p = p + 2;
        if p + fl > end {
            return e_malformed();
        }
        if !clean(data, p, fl) {
            return e_malformed();
        }
        t[2 + 3 * n] = p;
        t[3 + 3 * n] = fl;
        p = p + fl;
        var q = 0;
        if with_qos {
            if p >= end {
                return e_malformed();
            }
            q = int_of(data[p]);
            // [MQTT-3.8.3-4] the upper six bits are zero, and QoS is 0 to 2.
            if q > 2 {
                return e_malformed();
            }
            p = p + 1;
        }
        t[4 + 3 * n] = q;
        n = n + 1;
        t[1] = n;
    }
    return 0;
}

// ---- PUBACK, PUBREC, PUBREL, PUBCOMP -------------------------------------

// The packet id of a PUBACK, PUBREC, PUBREL or PUBCOMP (all four are a two-byte
// body holding only the identifier), or the refusal.
pub fn decode_puback[&d](data: &d [byte]) -> [] int {
    if len(data) != 4 {
        return e_malformed();
    }
    let pid = be16(data, 2, 4);
    if pid == 0 {
        return e_packet_id();
    }
    return pid;
}

// A packet with no body: PINGREQ and DISCONNECT.
pub fn decode_empty[&d](data: &d [byte]) -> [] int {
    if len(data) != 2 {
        return e_malformed();
    }
    return 0;
}

// ---- encoding ------------------------------------------------------------

// Bytes the remaining length `n` takes.
fn length_bytes(n: int) -> [] int {
    if n < 128 {
        return 1;
    }
    if n < 16384 {
        return 2;
    }
    if n < 2097152 {
        return 3;
    }
    return 4;
}

// Write the first byte and the remaining length at `at`; answers the offset after.
// The caller has checked there is room.
fn put_header[&o](out: &!o [byte], at: int, first: int, remaining: int) -> [] int {
    out[at] = byte_of(first);
    var p = at + 1;
    var n = remaining;
    var going = true;
    while going {
        var b = n % 128;
        n = n / 128;
        if n > 0 {
            b = b + 128;
        } else {
            going = false;
        }
        out[p] = byte_of(b);
        p = p + 1;
    }
    return p;
}

fn put16[&o](out: &!o [byte], at: int, v: int) -> [] int {
    out[at] = byte_of(v >> 8 & 255);
    out[at + 1] = byte_of(v & 255);
    return at + 2;
}

// CONNACK: 4 bytes. `present` is the session-present flag, `code` the return code.
pub fn put_connack[&o](out: &!o [byte], at: int, present: int, code: int) -> [] int {
    if at < 0 || at + 4 > len(out) {
        return 0 - 1;
    }
    out[at] = byte_of(32);
    out[at + 1] = byte_of(2);
    out[at + 2] = byte_of(present & 1);
    out[at + 3] = byte_of(code);
    return at + 4;
}

pub fn put_puback[&o](out: &!o [byte], at: int, pid: int) -> [] int {
    if at < 0 || at + 4 > len(out) {
        return 0 - 1;
    }
    out[at] = byte_of(64);
    out[at + 1] = byte_of(2);
    return put16(out, at + 2, pid);
}

// PUBREC (first byte 0x50), PUBREL (0x62: bit 1 of the flags is set, 3.6.1) and
// PUBCOMP (0x70): a PUBACK with another first byte.
pub fn put_pubrec[&o](out: &!o [byte], at: int, pid: int) -> [] int {
    if at < 0 || at + 4 > len(out) {
        return 0 - 1;
    }
    out[at] = byte_of(80);
    out[at + 1] = byte_of(2);
    return put16(out, at + 2, pid);
}

pub fn put_pubrel[&o](out: &!o [byte], at: int, pid: int) -> [] int {
    if at < 0 || at + 4 > len(out) {
        return 0 - 1;
    }
    out[at] = byte_of(98);
    out[at + 1] = byte_of(2);
    return put16(out, at + 2, pid);
}

pub fn put_pubcomp[&o](out: &!o [byte], at: int, pid: int) -> [] int {
    if at < 0 || at + 4 > len(out) {
        return 0 - 1;
    }
    out[at] = byte_of(112);
    out[at + 1] = byte_of(2);
    return put16(out, at + 2, pid);
}

pub fn put_unsuback[&o](out: &!o [byte], at: int, pid: int) -> [] int {
    if at < 0 || at + 4 > len(out) {
        return 0 - 1;
    }
    out[at] = byte_of(176);
    out[at + 1] = byte_of(2);
    return put16(out, at + 2, pid);
}

pub fn put_pingresp[&o](out: &!o [byte], at: int) -> [] int {
    if at < 0 || at + 2 > len(out) {
        return 0 - 1;
    }
    out[at] = byte_of(208);
    out[at + 1] = byte_of(0);
    return at + 2;
}

// Bytes a SUBACK with `n` return codes takes.
pub fn suback_size(n: int) -> [] int {
    return 1 + length_bytes(2 + n) + 2 + n;
}

// SUBACK with the first `n` return codes of `codes`.
pub fn put_suback[&o, &c](out: &!o [byte], at: int, pid: int, codes: &c [int], n: int) -> [] int {
    let remaining = 2 + n;
    if at < 0 || n < 0 || n > len(codes) || at + 1 + length_bytes(remaining) + remaining > len(out) {
        return 0 - 1;
    }
    var p = put_header(out, at, 144, remaining);
    p = put16(out, p, pid);
    var i = 0;
    while i < n {
        out[p] = byte_of(codes[i]);
        p = p + 1;
        i = i + 1;
    }
    return p;
}

// Size of the PUBLISH that `put_publish` would write; the caller checks it against
// its queue before writing.
pub fn publish_size(topic_len: int, payload_len: int, qos: int) -> [] int {
    var remaining = 2 + topic_len + payload_len;
    if qos > 0 {
        remaining = remaining + 2;
    }
    return 1 + length_bytes(remaining) + remaining;
}

// PUBLISH. Answers the offset after it, or -1 when it does not fit in `out`.
pub fn put_publish[&o, &t, &p](out: &!o [byte], at: int, topic: &t [byte], payload: &p [byte], qos: int, retain: int, dup: int, pid: int) -> [] int {
    let size = publish_size(len(topic), len(payload), qos);
    if at < 0 || at + size > len(out) || len(topic) > string_limit() {
        return 0 - 1;
    }
    var remaining = 2 + len(topic) + len(payload);
    if qos > 0 {
        remaining = remaining + 2;
    }
    var first = 48 + (qos << 1) + (retain & 1);
    if dup == 1 && qos > 0 {
        first = first + 8;
    }
    var q = put_header(out, at, first, remaining);
    q = put16(out, q, len(topic));
    q = q + copy_into(out[q..len(out)], topic);
    if qos > 0 {
        q = put16(out, q, pid);
    }
    q = q + copy_into(out[q..len(out)], payload);
    return q;
}
