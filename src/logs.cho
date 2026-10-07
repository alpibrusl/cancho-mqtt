edition 5;

// `mqtt.logs` -- the log stream (design section 5a): one JSON object per line on
// standard output, every record of a type `schemas/mqtt.v1.json` lists, ending
// with an `end` record. Bounded by construction: `refusal` is written at most once
// a second per rule (the repeats are counted, and the count is written as one more
// `refusal` record when the second has passed), `stats` once an interval, and no
// message payload is ever written. Client identifiers are `text_or_bytes`, cut at
// the bytes the event kept, with `truncated` saying so.
//
// `--format text` writes the same records as lines of `key=value` for a person:
// lossy by design (an identifier's unprintable bytes are `?`), never to be parsed.
//
// Every writer answers false when standard output took fewer bytes than were
// written or could not be flushed; the caller then ends the stream.

module mqtt.logs;

import std.buffer;
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

fn sent_text[&h, &i](heap: &!h Heap, io: &!i Io, b: buffer.Buffer) -> [heap, io_write] bool {
    if !out.buffer_line(heap, io, b) {
        return false;
    }
    return out.flushed(io);
}

// ` key=n` on a text line.
fn kv[&h](heap: &!h Heap, b: buffer.Buffer, key: &static [byte], n: int) -> [heap] buffer.Buffer {
    var o = buffer.append(heap, b, " ");
    o = buffer.append(heap, o, key);
    o = buffer.append(heap, o, "=");
    if n < 0 {
        o = buffer.append(heap, o, "-1");
        return o;
    }
    return buffer.push_nat(heap, o, n);
}

// `{"type":"listening", ...}`
pub fn listening[&h, &i](heap: &!h Heap, io: &!i Io, text_mode: bool, port: int, connections: int, packet: int, memory: int, t_ms: int) -> [heap, io_write] bool {
    if text_mode {
        var b = buffer.append(heap, buffer.empty(heap, 128), "listening");
        b = kv(heap, b, "port", port);
        b = kv(heap, b, "max_connections", connections);
        b = kv(heap, b, "max_packet", packet);
        b = kv(heap, b, "memory_bytes", memory);
        b = kv(heap, b, "t_ms", t_ms);
        return sent_text(heap, io, b);
    }
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

// A refusal record: `connection` -1 and no identifier for the summary of repeats.
fn refusal_record[&h, &i, &d](heap: &!h Heap, io: &!i Io, text_mode: bool, rule: int, connection: int, id: &d [byte], has_id: bool, full_id: int, t_ms: int, suppressed: int) -> [heap, io_write] bool {
    if text_mode {
        var b = buffer.append(heap, buffer.empty(heap, 128), "refusal rule=");
        b = buffer.append(heap, b, rules.tag(rule));
        b = kv(heap, b, "connection", connection);
        b = buffer.append(heap, b, " client_id=");
        if has_id {
            var j = 0;
            while j < len(id) {
                var c = int_of(id[j]);
                if c < 33 || c > 126 {
                    c = 63;
                }
                b = buffer.push(heap, b, byte_of(c));
                j = j + 1;
            }
        } else {
            b = buffer.append(heap, b, "-");
        }
        b = kv(heap, b, "suppressed", suppressed);
        b = kv(heap, b, "t_ms", t_ms);
        return sent_text(heap, io, b);
    }
    var w = json.writer(heap, 256);
    w = json.begin_object(heap, w);
    w = json.put_key(heap, w, "type");
    w = json.put_string(heap, w, "refusal");
    w = json.put_key(heap, w, "rule");
    w = json.put_string(heap, w, rules.tag(rule));
    w = json.put_key(heap, w, "connection");
    w = json.put_int(heap, w, connection);
    w = json.put_key(heap, w, "client_id");
    if !has_id {
        w = json.put_null(heap, w);
    } else {
        w = text.put(heap, w, id);
    }
    w = json.put_key(heap, w, "truncated");
    w = json.put_bool(heap, w, full_id > len(id));
    w = json.put_key(heap, w, "t_ms");
    w = json.put_int(heap, w, t_ms);
    w = json.put_key(heap, w, "suppressed");
    w = json.put_int(heap, w, suppressed);
    return sent(heap, io, w);
}

// Write the refusals that happened since the last call. The broker leaves one event
// per rule per second and counts every occurrence exactly, so a record's `suppressed` is
// the counter's growth since the rule's last record, less the occurrence it describes;
// occurrences after the last event of a second are written as a summary record (connection
// -1, no identifier) once that second has passed. `limiter` holds two numbers per rule:
// the second after the one it last wrote in, and the counter when it last wrote. `now` is
// in seconds, `t_ms` the time for the records. False if a write fell short.
pub fn drain[&h, &i, &b, &l](heap: &!h Heap, io: &!i Io, text_mode: bool, b: &!b broker.Broker, limiter: &!l [int], now: int, t_ms: int) -> [heap, io_write] bool {
    var ok = true;
    while ok && broker.events_waiting(b) > 0 {
        let rule = broker.event_field(b, 0);
        let seen = broker.counter(b, rule);
        var held = seen - limiter[2 * rule + 1] - 1;
        if held < 0 {
            held = 0;
        }
        ok = refusal_record(heap, io, text_mode, rule, broker.event_field(b, 1), broker.event_id(b), broker.event_field(b, 3) >= 0, broker.event_field(b, 4), broker.event_field(b, 2), held);
        limiter[2 * rule] = now + 1;
        limiter[2 * rule + 1] = seen;
        broker.event_pop(b);
    }
    // Repeats after the last event of a second that has passed are reported without
    // waiting for another event of that rule.
    var r = 0;
    while ok && r < rules.count() {
        let seen = broker.counter(b, r);
        if seen > limiter[2 * r + 1] && limiter[2 * r] <= now {
            ok = refusal_record(heap, io, text_mode, r, 0 - 1, "", false, 0, t_ms, seen - limiter[2 * r + 1]);
            limiter[2 * r + 1] = seen;
        }
        r = r + 1;
    }
    return ok;
}

// Write every held count, whatever second it is: the stream is ending.
pub fn drain_all[&h, &i, &b, &l](heap: &!h Heap, io: &!i Io, text_mode: bool, b: &b broker.Broker, limiter: &!l [int], t_ms: int) -> [heap, io_write] bool {
    var ok = true;
    var r = 0;
    while ok && r < rules.count() {
        let seen = broker.counter(b, r);
        if seen > limiter[2 * r + 1] {
            ok = refusal_record(heap, io, text_mode, r, 0 - 1, "", false, 0, t_ms, seen - limiter[2 * r + 1]);
            limiter[2 * r + 1] = seen;
        }
        r = r + 1;
    }
    return ok;
}

// `{"type":"stats", ...}`: gauges, totals and one counter per rule.
pub fn stats[&h, &i, &b](heap: &!h Heap, io: &!i Io, text_mode: bool, b: &b broker.Broker, t_ms: int) -> [heap, io_write] bool {
    if text_mode {
        var o = buffer.append(heap, buffer.empty(heap, 256), "stats");
        o = kv(heap, o, "t_ms", t_ms);
        o = kv(heap, o, "connections", broker.connections(b));
        o = kv(heap, o, "sessions_online", broker.online(b));
        o = kv(heap, o, "sessions_offline", broker.offline(b));
        o = kv(heap, o, "subscriptions", broker.subscriptions(b));
        o = kv(heap, o, "retained", broker.retained(b));
        o = totals_text(heap, o, b);
        var r = 0;
        while r < rules.count() {
            if broker.counter(b, r) > 0 {
                o = buffer.append(heap, o, " ");
                o = buffer.append(heap, o, rules.tag(r));
                o = buffer.append(heap, o, "=");
                o = buffer.push_nat(heap, o, broker.counter(b, r));
            }
            r = r + 1;
        }
        return sent_text(heap, io, o);
    }
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

fn totals_text[&h, &b](heap: &!h Heap, t: buffer.Buffer, b: &b broker.Broker) -> [heap] buffer.Buffer {
    var o = t;
    o = kv(heap, o, "publishes", broker.counter(b, tables.k_publishes()));
    o = kv(heap, o, "delivered", broker.counter(b, tables.k_delivered()));
    o = kv(heap, o, "connects", broker.counter(b, tables.k_connects()));
    o = kv(heap, o, "disconnects", broker.counter(b, tables.k_disconnects()));
    o = kv(heap, o, "retained_sent", broker.counter(b, tables.k_retained_sent()));
    o = kv(heap, o, "offline_dropped", broker.counter(b, tables.k_offline_dropped()));
    return kv(heap, o, "takeovers", broker.counter(b, tables.k_takeovers()));
}

// The `end` record of a stream that ran: complete, with the totals.
pub fn end_served[&h, &i, &b](heap: &!h Heap, io: &!i Io, text_mode: bool, b: &b broker.Broker, t_ms: int) -> [heap, io_write] bool {
    if text_mode {
        var o = buffer.append(heap, buffer.empty(heap, 128), "end ok=true complete=true");
        o = kv(heap, o, "t_ms", t_ms);
        o = totals_text(heap, o, b);
        return sent_text(heap, io, o);
    }
    var w = out.end_open(heap, "mqtt", "mqtt.v1", true, true);
    w = json.put_key(heap, w, "t_ms");
    w = json.put_int(heap, w, t_ms);
    w = totals(heap, w, b);
    return sent(heap, io, w);
}

// `{"type":"rule", ...}` for rule `r`: what `mqtt rules` lists.
pub fn rule_record[&h, &i](heap: &!h Heap, io: &!i Io, text_mode: bool, r: int) -> [heap, io_write] bool {
    if text_mode {
        var o = buffer.append(heap, buffer.empty(heap, 96), rules.tag(r));
        o = buffer.append(heap, o, "  ");
        o = buffer.append(heap, o, rules.action(r));
        return sent_text(heap, io, o);
    }
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
