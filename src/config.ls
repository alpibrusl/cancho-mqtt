edition 5;

// `mqtt.config` -- every bound of design section 4 as a flag (docs/design.md
// section 5a): one table drives the parser, `introspect` and `skill`, and the
// broker reads the values by the index each flag has in that table. Every flag
// has role `none`, so no repair that raises a bound can widen authority.
//
// Table entries are `name|short|kind|role|default|help`, `;`-separated, in the
// order of the `i_*` functions below. `limits` gives each numeric flag's
// ceiling, `name|default|ceiling`, for `introspect` and for `args.out-of-range`.

module mqtt.config;

import std.bytes;

pub fn flag_table() -> [] &static [byte] {
    return "port||nat|none|1883|the TCP port to listen on;max-connections||nat|none|1024|connections at once, at most;max-packet||nat|none|16384|largest packet in bytes, header included;queue-bytes||nat|none|16384|bytes of outbound queue per session;queue-messages||nat|none|64|messages in one session's outbound queue;inflight||nat|none|16|QoS 1 messages sent and not yet acknowledged, per session;offline-sessions||nat|none|256|sessions kept for clients that are not connected;subscriptions-per-client||nat|none|32|subscriptions one client may hold;subscriptions-total||nat|none|16384|subscriptions in all;max-nodes||nat|none|65536|nodes in the subscription trie;topic-max||nat|none|1024|longest topic name or filter in bytes;topic-levels||nat|none|16|most levels in a topic name or filter;retained-messages||nat|none|1024|retained messages kept;retained-slot-bytes||nat|none|1024|bytes one retained message may take, topic included;will-bytes||nat|none|1024|bytes a will message may take, topic included;client-id-max||nat|none|128|longest client identifier in bytes;connect-timeout||nat|none|10|seconds a connection may take to send CONNECT;write-stall||nat|none|30|seconds a connection may make no write progress;stats-seconds||nat|none|10|seconds between stats records, 0 for none";
}

pub fn flags() -> [] int {
    return 19;
}

// `name|default|ceiling`, `;`-separated, in the same order as `flag_table`.
pub fn limits() -> [] &static [byte] {
    return "port|1883|65535;max-connections|1024|16384;max-packet|16384|262144;queue-bytes|16384|1048576;queue-messages|64|1024;inflight|16|64;offline-sessions|256|16384;subscriptions-per-client|32|1024;subscriptions-total|16384|1048576;max-nodes|65536|4194304;topic-max|1024|65535;topic-levels|16|64;retained-messages|1024|65536;retained-slot-bytes|1024|262144;will-bytes|1024|65536;client-id-max|128|1024;connect-timeout|10|3600;write-stall|30|86400;stats-seconds|10|86400";
}

pub fn i_port() -> [] int {
    return 0;
}

pub fn i_connections() -> [] int {
    return 1;
}

pub fn i_packet() -> [] int {
    return 2;
}

pub fn i_queue_bytes() -> [] int {
    return 3;
}

pub fn i_queue_messages() -> [] int {
    return 4;
}

pub fn i_inflight() -> [] int {
    return 5;
}

pub fn i_offline() -> [] int {
    return 6;
}

pub fn i_subs_client() -> [] int {
    return 7;
}

pub fn i_subs_total() -> [] int {
    return 8;
}

pub fn i_nodes() -> [] int {
    return 9;
}

pub fn i_topic_max() -> [] int {
    return 10;
}

pub fn i_levels() -> [] int {
    return 11;
}

pub fn i_retained() -> [] int {
    return 12;
}

pub fn i_retained_slot() -> [] int {
    return 13;
}

pub fn i_will() -> [] int {
    return 14;
}

pub fn i_client_id() -> [] int {
    return 15;
}

pub fn i_connect_timeout() -> [] int {
    return 16;
}

pub fn i_write_stall() -> [] int {
    return 17;
}

pub fn i_stats() -> [] int {
    return 18;
}

// The decimal number in `text`, or -1 for empty text, a non-digit, or more than 17 digits.
pub fn number[&t](text: &t [byte]) -> [] int {
    if len(text) == 0 || len(text) > 17 {
        return 0 - 1;
    }
    var n = 0;
    var i = 0;
    while i < len(text) {
        let c = int_of(text[i]);
        if c < 48 || c > 57 {
            return 0 - 1;
        }
        n = n * 10 + (c - 48);
        i = i + 1;
    }
    return n;
}

// Entry `i` (0-based) of `limits`: its default and its ceiling. `bytes.field` counts from 1.
pub fn default_of(i: int) -> [] int {
    return number(bytes.field(bytes.field(limits(), 59, i + 1), 124, 2));
}

pub fn ceiling_of(i: int) -> [] int {
    return number(bytes.field(bytes.field(limits(), 59, i + 1), 124, 3));
}
