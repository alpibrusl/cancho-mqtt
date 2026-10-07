edition 5;

// `mqtt.rules` -- the broker's connection-level rules, as numbers the hot path
// can count and as the tags (`<area>.<name>`, lex-sys docs/agent-toolbox.md D5)
// the logs, the counters and `introspect` name. One list: `tag` is the only place
// a tag is spelled, and `describe` reads `count` and `tag`, so the catalogue and
// the code cannot disagree.

module mqtt.rules;

import mqtt.wire;

pub fn connect_first() -> [] int {
    return 0;
}

pub fn connect_twice() -> [] int {
    return 1;
}

pub fn connect_timeout() -> [] int {
    return 2;
}

pub fn bad_name() -> [] int {
    return 3;
}

pub fn unsupported_level() -> [] int {
    return 4;
}

pub fn client_id() -> [] int {
    return 5;
}

pub fn reserved_flags() -> [] int {
    return 6;
}

pub fn remaining_length() -> [] int {
    return 7;
}

pub fn packet_size() -> [] int {
    return 8;
}

pub fn malformed() -> [] int {
    return 9;
}

pub fn topic_invalid() -> [] int {
    return 10;
}

pub fn filter_invalid() -> [] int {
    return 11;
}

pub fn packet_id() -> [] int {
    return 12;
}

pub fn qos3() -> [] int {
    return 13;
}

pub fn subs_per_client() -> [] int {
    return 14;
}

pub fn subs_total() -> [] int {
    return 15;
}

pub fn connections() -> [] int {
    return 16;
}

pub fn keepalive() -> [] int {
    return 17;
}

pub fn write_stalled() -> [] int {
    return 18;
}

pub fn queue() -> [] int {
    return 19;
}

pub fn retained() -> [] int {
    return 20;
}

pub fn offline_sessions() -> [] int {
    return 21;
}

pub fn unexpected_packet() -> [] int {
    return 22;
}

pub fn topic_level() -> [] int {
    return 23;
}

pub fn will_size() -> [] int {
    return 24;
}

pub fn output_full() -> [] int {
    return 25;
}

pub fn qos2_inbound() -> [] int {
    return 26;
}

pub fn count() -> [] int {
    return 27;
}

// The tag of rule `i`.
pub fn tag(i: int) -> [] &static [byte] {
    if i == 0 {
        return "protocol.connect-first";
    }
    if i == 1 {
        return "protocol.connect-twice";
    }
    if i == 2 {
        return "timeout.connect";
    }
    if i == 3 {
        return "protocol.bad-name";
    }
    if i == 4 {
        return "protocol.unsupported-level";
    }
    if i == 5 {
        return "protocol.client-id-rejected";
    }
    if i == 6 {
        return "protocol.reserved-flags";
    }
    if i == 7 {
        return "protocol.remaining-length";
    }
    if i == 8 {
        return "limit.packet-size";
    }
    if i == 9 {
        return "protocol.malformed-packet";
    }
    if i == 10 {
        return "protocol.topic-invalid";
    }
    if i == 11 {
        return "protocol.filter-invalid";
    }
    if i == 12 {
        return "protocol.packet-id";
    }
    if i == 13 {
        return "protocol.qos3";
    }
    if i == 14 {
        return "limit.subscriptions-per-client";
    }
    if i == 15 {
        return "limit.subscriptions-total";
    }
    if i == 16 {
        return "limit.connections";
    }
    if i == 17 {
        return "timeout.keepalive";
    }
    if i == 18 {
        return "timeout.write-stalled";
    }
    if i == 19 {
        return "limit.queue";
    }
    if i == 20 {
        return "limit.retained";
    }
    if i == 21 {
        return "limit.offline-sessions";
    }
    if i == 22 {
        return "protocol.unexpected-packet";
    }
    if i == 23 {
        return "limit.topic-level";
    }
    if i == 24 {
        return "limit.will-size";
    }
    if i == 25 {
        return "limit.output-full";
    }
    return "limit.qos2-inbound";
}

// What the broker does when the rule fires, for `introspect`'s `connection_rules`.
pub fn action(i: int) -> [] &static [byte] {
    if i == 4 {
        return "CONNACK 0x01, then close";
    }
    if i == 5 {
        return "CONNACK 0x02, then close";
    }
    if i == 11 || i == 14 || i == 15 || i == 23 {
        return "SUBACK 0x80 for that filter";
    }
    if i == 16 {
        return "close at accept";
    }
    if i == 19 {
        return "drop the message for that subscriber";
    }
    if i == 20 {
        return "deliver live, do not retain";
    }
    if i == 21 {
        return "evict the oldest offline session";
    }
    if i == 24 {
        return "CONNACK 0x03, then close";
    }
    return "close";
}

// The rule a wire code stands for, or -1 for a code that is not a refusal.
pub fn of_wire(code: int) -> [] int {
    if code == wire.bad_length() {
        return remaining_length();
    }
    if code == wire.too_large() {
        return packet_size();
    }
    if code == wire.e_malformed() {
        return malformed();
    }
    if code == wire.e_reserved() {
        return reserved_flags();
    }
    if code == wire.e_name() {
        return bad_name();
    }
    if code == wire.e_level() {
        return unsupported_level();
    }
    if code == wire.e_client_id() {
        return client_id();
    }
    if code == wire.e_qos3() {
        return qos3();
    }
    if code == wire.e_topic() {
        return topic_invalid();
    }
    if code == wire.e_packet_id() {
        return packet_id();
    }
    if code == wire.e_unexpected() {
        return unexpected_packet();
    }
    return 0 - 1;
}
