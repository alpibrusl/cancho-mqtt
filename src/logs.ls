edition 5;

// `mqtt.logs` -- the log stream (design section 5a): one JSON object per line on
// standard output, every record of a type `schemas/mqtt.v1.json` lists, ending
// with an `end` record. Bounded by construction: `refusal` is written at most once
// a second per rule (the rest are counted into `suppressed`), `stats` once an
// interval, and no message payload is ever written. Client identifiers are
// `text_or_bytes`, cut at the bytes the event kept, with `truncated` saying so.
//
// Every writer answers false when standard output took fewer bytes than were
// written; the caller then ends the stream.

module mqtt.logs;

import std.json;
import toolbox.out;
import toolbox.text;
import mqtt.broker;
import mqtt.rules;
import mqtt.tables;

// Write a record and push it out: standard output on a pipe is block-buffered, and a
// supervisor reading the log must see a record when it is written, not when the
// buffer fills. False if the write or the flush failed.
fn sent[&h, &i](heap: &!h Heap, io: &!i Io, w: json.Writer) -> [heap, io_write] bool {
    if !out.close_line(heap, io, w) {
        return false;
    }
    return out.flushed(io);
}

// `{"type":"listening", ...}`
pub fn listening[&h, &i](heap: &!h Heap, io: &!i Io, port: int, connections: int, packet: int, memory: int, t_ms: int) -> [heap, io_write] bool {
    var w = json.writer(heap, 256);
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "type");
    w = json.put_string(heap, w, "listening");
    w = json.put_key(heap, w, "port");
    w = json.put_int(heap, w, port);
    w = json.put_key(heap, w, "max_connections");
    w = json.put_int(heap, w, connections);
    w = json.put_key(heap, w, "max_packet");
    w = json.put_int(heap, w, packet);
    w = json.put_key(heap, w, "memory_bytes");
    w = json.put_int(heap, w, memory);
    w = json.put_key(heap, w, "t_ms");
    w = json.put_int(heap, w, t_ms);
    return sent(heap, io, w);
}

// One refusal, from the event at the front of `b`'s queue.
fn refusal[&h, &i, &b](heap: &!h Heap, io: &!i Io, b: &b broker.Broker, suppressed: int) -> [heap, io_write] bool {
    var w = json.writer(heap, 256);
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "type");
    w = json.put_string(heap, w, "refusal");
    w = json.put_key(heap, w, "rule");
    w = json.put_string(heap, w, rules.tag(broker.event_field(b, 0)));
    w = json.put_key(heap, w, "connection");
    w = json.put_int(heap, w, broker.event_field(b, 1));
    w = json.put_key(heap, w, "client_id");
    let full = broker.event_field(b, 4);
    if broker.event_field(b, 3) < 0 {
        w = json.put_null(heap, w);
    } else {
        w = text.put(heap, w, broker.event_id(b));
    }
    w = json.put_key(heap, w, "truncated");
    w = json.put_bool(heap, w, full > len(broker.event_id(b)));
    w = json.put_key(heap, w, "t_ms");
    w = json.put_int(heap, w, broker.event_field(b, 2));
    w = json.put_key(heap, w, "suppressed");
    w = json.put_int(heap, w, suppressed);
    return sent(heap, io, w);
}

// Write the refusals that happened since the last call: the first of each rule in
// each second, its repeats counted. `limiter` holds two numbers per rule (the
// second it last wrote, and how many it has held back since); `now` is in seconds.
// False if a write fell short.
pub fn drain[&h, &i, &b, &l](heap: &!h Heap, io: &!i Io, b: &!b broker.Broker, limiter: &!l [int], now: int) -> [heap, io_write] bool {
    var ok = true;
    while ok && broker.events_waiting(b) > 0 {
        let rule = broker.event_field(b, 0);
        if limiter[2 * rule + 1] >= 0 && limiter[2 * rule] == now + 1 {
            limiter[2 * rule + 1] = limiter[2 * rule + 1] + 1;
        } else {
            var held = 0;
            if limiter[2 * rule + 1] > 0 {
                held = limiter[2 * rule + 1];
            }
            limiter[2 * rule] = now + 1;
            limiter[2 * rule + 1] = 0;
            ok = refusal(heap, io, b, held);
        }
        broker.event_pop(b);
    }
    return ok;
}

// `{"type":"stats", ...}`: gauges, totals and one counter per rule.
pub fn stats[&h, &i, &b](heap: &!h Heap, io: &!i Io, b: &b broker.Broker, t_ms: int) -> [heap, io_write] bool {
    var w = json.writer(heap, 1024);
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "type");
    w = json.put_string(heap, w, "stats");
    w = json.put_key(heap, w, "t_ms");
    w = json.put_int(heap, w, t_ms);
    w = json.put_key(heap, w, "connections");
    w = json.put_int(heap, w, broker.connections(b));
    w = json.put_key(heap, w, "sessions_online");
    w = json.put_int(heap, w, broker.online(b));
    w = json.put_key(heap, w, "sessions_offline");
    w = json.put_int(heap, w, broker.offline(b));
    w = json.put_key(heap, w, "subscriptions");
    w = json.put_int(heap, w, broker.subscriptions(b));
    w = json.put_key(heap, w, "retained");
    w = json.put_int(heap, w, broker.retained(b));
    w = totals(heap, w, b);
    w = json.put_key(heap, w, "refusals");
    w = json.begin_object(heap, w);
    var r = 0;
    while r < rules.count() {
        w = json.put_key(heap, w, rules.tag(r));
        w = json.put_int(heap, w, broker.counter(b, r));
        r = r + 1;
    }
    w = json.end_object(heap, w);
    return sent(heap, io, w);
}

// The totals both `stats` and `end` carry.
fn totals[&h, &b](heap: &!h Heap, w: json.Writer, b: &b broker.Broker) -> [heap] json.Writer {
    var o = w;
    o = json.put_key(heap, o, "publishes");
    o = json.put_int(heap, o, broker.counter(b, tables.k_publishes()));
    o = json.put_key(heap, o, "delivered");
    o = json.put_int(heap, o, broker.counter(b, tables.k_delivered()));
    o = json.put_key(heap, o, "connects");
    o = json.put_int(heap, o, broker.counter(b, tables.k_connects()));
    o = json.put_key(heap, o, "disconnects");
    o = json.put_int(heap, o, broker.counter(b, tables.k_disconnects()));
    o = json.put_key(heap, o, "retained_sent");
    o = json.put_int(heap, o, broker.counter(b, tables.k_retained_sent()));
    o = json.put_key(heap, o, "offline_dropped");
    o = json.put_int(heap, o, broker.counter(b, tables.k_offline_dropped()));
    o = json.put_key(heap, o, "takeovers");
    return json.put_int(heap, o, broker.counter(b, tables.k_takeovers()));
}

// The `end` record of a stream that ran: complete, with the totals.
pub fn end_served[&h, &i, &b](heap: &!h Heap, io: &!i Io, b: &b broker.Broker, t_ms: int) -> [heap, io_write] bool {
    var w = out.end_open(heap, "mqtt", "mqtt.v1", true, true);
    w = json.put_key(heap, w, "t_ms");
    w = json.put_int(heap, w, t_ms);
    w = totals(heap, w, b);
    return sent(heap, io, w);
}

// `{"type":"rule", ...}` for rule `i`: what `mqtt rules` lists.
pub fn rule_record[&h, &i](heap: &!h Heap, io: &!i Io, r: int) -> [heap, io_write] bool {
    var w = json.writer(heap, 256);
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "type");
    w = json.put_string(heap, w, "rule");
    w = json.put_key(heap, w, "tag");
    w = json.put_string(heap, w, rules.tag(r));
    w = json.put_key(heap, w, "action");
    w = json.put_string(heap, w, rules.action(r));
    return sent(heap, io, w);
}
