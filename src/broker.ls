edition 5;

// `mqtt.broker` -- connections, packets and routing, on one `Poller` (design
// sections 3 to 9). The shape is `http.server`'s: `wait` does the I/O that is
// ready, then everything that is to be sent is flushed once, so a burst of
// publishes to one subscriber becomes one write.
//
// Every refusal is counted under its rule (`tables.refuse`) and acted on as
// section 5 says. No packet is handled before `wire.header` has said how long it
// is, and none longer than the bound is ever buffered.

module mqtt.broker;

import std.conns;
import mqtt.config;
import mqtt.rules;
import mqtt.subs;
import mqtt.tables;
import mqtt.topic;
import mqtt.wire;

pub res struct Broker {
    tab: conns.Table,
    core: tables.Core,
}

// ---- opening and closing -------------------------------------------------

// A broker for connections on `listener` (already listening and non-blocking),
// waited on through `poller`, which it owns from now on. `cfg` is indexed by
// `config.i_*`.
pub fn open[&h, &l, &g](heap: &!h Heap, poller: Poller, listener: &!l Listener, cfg: &g [int]) -> [heap] Broker {
    var p = poller;
    borrow mut p as &!pw in {
        poller_add_listener(pw, listener, 0);
    }
    return Broker { tab: conns.empty(heap, 64), core: tables.open(heap, p, cfg) };
}

pub fn close[&h](heap: &!h Heap, b: Broker) -> [heap] int {
    let Broker { tab, core } = b;
    conns.drop(heap, tab);
    tables.drop(heap, core);
    return 0;
}

pub fn connections[&b](b: &b Broker) -> [] int {
    return conns.live(b.tab);
}

// ---- the table of connections -------------------------------------------

// Write what there is to say before a session exists (or after it has gone),
// without queueing: best effort, a fresh connection always has room for four
// bytes.
fn send_direct[&t, &d](tab: &!t conns.Table, k: int, data: &d [byte]) -> [conn_write] int {
    match conns.write(tab, k, data) {
        Sent::Wrote(n) => {
            return n;
        }
        Sent::Again => {
            return 0;
        }
        Sent::Failed(e) => {
            return 0 - 1;
        }
    }
}

// End connection `k`: its socket, its slot. Its session is the caller's business.
fn shut[&t, &c](tab: &!t conns.Table, core: &!c tables.Core, k: int) -> [] int {
    let cd = contents(core.cs);
    conns.close(tab, k);
    let p = tables.c_stride() * k;
    cd[p] = 0;
    cd[p + 1] = 0;
    cd[p + 3] = 0 - 1;
    cd[p + 6] = 0;
    cd[p + 8] = 0;
    cd[p + 9] = 0;
    return 0;
}

// Queue `k` to be flushed at the end of this round.
fn mark_dirty[&c](core: &!c tables.Core, k: int) -> [] int {
    let cd = contents(core.cs);
    let p = tables.c_stride() * k;
    if cd[p + 10] == 0 && core.ndirty < len(contents(core.dirty)) {
        cd[p + 10] = 1;
        contents(core.dirty)[core.ndirty] = k;
        core.ndirty = core.ndirty + 1;
    }
    return 0;
}

// ---- queueing what is to be sent ----------------------------------------

// A PUBLISH for session `s` at `qos`, retain flag `retain`. False, and counted
// as `limit.queue`, when its queue has no room.
fn enqueue_publish[&c, &t, &p](core: &!c tables.Core, s: int, qos: int, name: &t [byte], payload: &p [byte], retain: int) -> [] bool {
    let size = wire.publish_size(len(name), len(payload), qos);
    let at = tables.q_reserve(core, s, size, true);
    if at < 0 {
        tables.refuse(core, rules.queue(), contents(core.ss)[tables.s_stride() * s + 1], s, 0);
        return false;
    }
    var pid = 0;
    if qos > 0 {
        pid = tables.q_pid(core, s);
    }
    wire.put_publish(tables.queue(core, s), at, name, payload, qos, retain, 0, pid);
    tables.q_commit(core, s, at, size, qos, pid);
    tables.bump(core, tables.k_delivered());
    let k = contents(core.ss)[tables.s_stride() * s + 1];
    if k >= 0 {
        mark_dirty(core, k);
    }
    return true;
}

// Which control packet `enqueue_control` writes.
fn ctl_puback() -> [] int {
    return 0;
}

fn ctl_unsuback() -> [] int {
    return 1;
}

fn ctl_pingresp() -> [] int {
    return 2;
}

// A PUBACK, UNSUBACK or PINGRESP for session `s`, ahead of any limit on
// messages. False when there is not even room for four bytes: the caller closes.
fn enqueue_control[&c](core: &!c tables.Core, s: int, which: int, pid: int) -> [] bool {
    var size = 4;
    if which == ctl_pingresp() {
        size = 2;
    }
    let at = tables.q_reserve(core, s, size, false);
    if at < 0 {
        return false;
    }
    let q = tables.queue(core, s);
    if which == ctl_puback() {
        wire.put_puback(q, at, pid);
    } else if which == ctl_unsuback() {
        wire.put_unsuback(q, at, pid);
    } else {
        wire.put_pingresp(q, at);
    }
    tables.q_commit(core, s, at, size, 2, 0);
    let k = contents(core.ss)[tables.s_stride() * s + 1];
    if k >= 0 {
        mark_dirty(core, k);
    }
    return true;
}

// ---- routing -------------------------------------------------------------

fn lower(a: int, b: int) -> [] int {
    if a < b {
        return a;
    }
    return b;
}

// A message arrived (or a will is being published): retain it if asked, then
// queue it for every matching subscriber, once each, at the lower of the QoS it
// came with and the QoS granted.
fn route[&c, &t, &p](core: &!c tables.Core, name: &t [byte], payload: &p [byte], qos: int, retain: int) -> [] int {
    tables.bump(core, tables.k_publishes());
    if retain == 1 {
        if tables.ret_set(core, name, payload, qos) == 1 {
            tables.refuse(core, rules.retained(), 0 - 1, 0 - 1, 0);
        }
    }
    let n = subs.find_matches(core.trie, name);
    var i = 0;
    while i < n {
        let s = subs.matched_session(core.trie, i);
        let eff = lower(qos, subs.matched_qos(core.trie, i));
        if contents(core.ss)[tables.s_stride() * s + 1] < 0 && eff == 0 {
            // A QoS 0 message is not kept for a client that is not there.
            tables.bump(core, tables.k_offline_dropped());
        } else {
            enqueue_publish(core, s, eff, name, payload, 0);
        }
        i = i + 1;
    }
    return 0;
}

// The retained messages that match `filter`, queued for session `s` at the lower of
// their QoS and `granted`, with the retain flag set.
fn deliver_retained[&c, &f](core: &!c tables.Core, s: int, filter: &f [byte], granted: int) -> [] int {
    var i = 0;
    while i < tables.ret_slots(core) {
        if tables.ret_used(core, i) && topic.matches(filter, tables.ret_topic(core, i)) {
            if enqueue_publish(core, s, lower(tables.ret_qos(core, i), granted), tables.ret_topic(core, i), tables.ret_payload(core, i), 1) {
                tables.bump(core, tables.k_retained_sent());
            }
        }
        i = i + 1;
    }
    return 0;
}

// Publish the will of session `s`.
fn publish_will[&c](core: &!c tables.Core, s: int) -> [] int {
    let sd = contents(core.ss);
    let p = tables.s_stride() * s;
    if sd[p + 13] != 1 {
        return 0;
    }
    sd[p + 13] = 0;
    let base = s * core.will_max;
    let tl = sd[p + 16];
    let ml = sd[p + 17];
    let w = contents(core.wbuf);
    route(core, w[base..base + tl], w[base + tl..base + tl + ml], sd[p + 14], sd[p + 18]);
    return 0;
}

// ---- ending a connection -------------------------------------------------

// The oldest offline session goes while there are more than the bound.
fn evict_offline[&c](core: &!c tables.Core) -> [] int {
    let sd = contents(core.ss);
    while core.offline > core.noffline {
        var victim = 0 - 1;
        var s = 0;
        while s < core.nsess {
            let p = tables.s_stride() * s;
            if sd[p] == 1 && sd[p + 1] < 0 {
                if victim < 0 || sd[p + 15] < sd[tables.s_stride() * victim + 15] {
                    victim = s;
                }
            }
            s = s + 1;
        }
        if victim < 0 {
            core.offline = 0;
        } else {
            tables.refuse(core, rules.offline_sessions(), 0 - 1, victim, 0);
            tables.free_session(core, victim);
            core.offline = core.offline - 1;
        }
    }
    return 0;
}

// End connection `k` and settle its session. `rule` is the refusal to count (or
// -1: a clean DISCONNECT, a reset). `will` is true when the will is to be
// published (an abrupt end), false for DISCONNECT and a takeover. `evict` is false
// while a takeover is about to resume the very session this leaves offline.
fn close_conn[&t, &c](tab: &!t conns.Table, core: &!c tables.Core, k: int, rule: int, will: bool, evict: bool) -> [] int {
    let cd = contents(core.cs);
    let sd = contents(core.ss);
    let p = tables.c_stride() * k;
    if cd[p] != 1 {
        return 0;
    }
    let s = cd[p + 3];
    if rule >= 0 {
        tables.refuse(core, rule, k, s, 0);
    }
    if s >= 0 {
        let sp = tables.s_stride() * s;
        sd[sp + 1] = 0 - 1;
        core.online = core.online - 1;
        if will {
            publish_will(core, s);
        } else {
            sd[sp + 13] = 0;
        }
        if sd[sp + 2] == 1 {
            tables.free_session(core, s);
        } else {
            tables.q_going_offline(core, s);
            sd[sp + 15] = core.now;
            core.offline = core.offline + 1;
            if evict {
                evict_offline(core);
            }
        }
    }
    shut(tab, core, k);
    tables.bump(core, tables.k_disconnects());
    return 0;
}

// ---- sending -------------------------------------------------------------

// Send what session `s` has queued on connection `k`, as far as the kernel and the
// in-flight window allow, and set what the connection is watched for. Answers 1
// if the connection has failed and is to be closed.
fn flush[&t, &c](tab: &!t conns.Table, core: &!c tables.Core, k: int) -> [conn_write, poll] int {
    let cd = contents(core.cs);
    let sd = contents(core.ss);
    let cp = tables.c_stride() * k;
    let s = cd[cp + 3];
    if cd[cp] != 1 || s < 0 {
        return 0;
    }
    let p = tables.s_stride() * s;
    let stage = contents(core.stage);
    var blocked = false;
    var failed = false;
    var going = true;
    while going {
        if sd[p + 8] >= sd[p + 7] {
            going = false;
        } else {
            // Copy what can go now -- as many queued messages as fit the staging buffer and
            // the in-flight window allow -- back to back, so that one write sends all of them.
            let q = tables.queue_view(core, s);
            var at = sd[p + 8];
            var staged = 0;
            var inflight = sd[p + 11];
            var skip = sd[p + 9];
            var more = true;
            while more && at < sd[p + 7] {
                let size = int_of(q[at]) << 24 | int_of(q[at + 1]) << 16 | int_of(q[at + 2]) << 8 | int_of(q[at + 3]);
                let kind = int_of(q[at + 5]);
                if kind == 1 && skip == 0 && inflight >= core.window {
                    more = false;
                } else {
                    var room = len(stage) - staged;
                    var take = size - skip;
                    if take > room {
                        take = room;
                    }
                    copy_into(stage[staged..staged + take], q[at + 8 + skip..at + 8 + skip + take]);
                    staged = staged + take;
                    if take < size - skip {
                        // The buffer is full in the middle of this message.
                        more = false;
                    } else {
                        if kind == 1 {
                            inflight = inflight + 1;
                        }
                        at = at + 8 + size;
                        skip = 0;
                    }
                }
            }
            if staged == 0 {
                going = false;
            } else {
                match conns.write(tab, k, stage[0..staged]) {
                    Sent::Wrote(n) => {
                        cd[cp + 9] = 0;
                        // Attribute the bytes the kernel took to the messages they finish.
                        let qm = tables.queue(core, s);
                        var left = n;
                        while left > 0 {
                            let e = sd[p + 8];
                            let size = int_of(qm[e]) << 24 | int_of(qm[e + 1]) << 16 | int_of(qm[e + 2]) << 8 | int_of(qm[e + 3]);
                            let kind = int_of(qm[e + 5]);
                            if left >= size - sd[p + 9] {
                                left = left - (size - sd[p + 9]);
                                if kind == 1 {
                                    qm[e + 4] = byte_of(1);
                                    sd[p + 11] = sd[p + 11] + 1;
                                } else {
                                    qm[e + 4] = byte_of(2);
                                    if kind == 0 {
                                        sd[p + 10] = sd[p + 10] - 1;
                                    }
                                }
                                sd[p + 8] = e + 8 + size;
                                sd[p + 9] = 0;
                            } else {
                                sd[p + 9] = sd[p + 9] + left;
                                left = 0;
                            }
                        }
                        if n < staged {
                            // The kernel took part of it: wait until it has room.
                            blocked = true;
                            going = false;
                        }
                    }
                    Sent::Again => {
                        blocked = true;
                        going = false;
                        if cd[cp + 9] == 0 {
                            cd[cp + 9] = core.now;
                            if core.now == 0 {
                                cd[cp + 9] = 1;
                            }
                        }
                    }
                    Sent::Failed(e) => {
                        failed = true;
                        going = false;
                    }
                }
            }
        }
    }
    if failed {
        return 1;
    }
    tables.q_trim(core, s);
    var want = 1;
    if blocked {
        want = 3;
    }
    if want != cd[cp + 5] {
        conns.rewatch(tab, core.poller, k, k + 1, want);
        cd[cp + 5] = want;
    }
    return 0;
}

// Flush every connection that has something new to send.
fn flush_dirty[&t, &c](tab: &!t conns.Table, core: &!c tables.Core) -> [conn_write, poll] int {
    let cd = contents(core.cs);
    var i = 0;
    while i < core.ndirty {
        let k = contents(core.dirty)[i];
        i = i + 1;
        cd[tables.c_stride() * k + 10] = 0;
        if cd[tables.c_stride() * k] == 1 {
            if flush(tab, core, k) == 1 {
                close_conn(tab, core, k, 0 - 1, true, true);
            }
        }
    }
    core.ndirty = 0;
    return 0;
}

// ---- packets -------------------------------------------------------------

fn digits[&o](out: &!o [byte], at: int, n: int) -> [] int {
    var width = 1;
    var m = n;
    while m >= 10 {
        m = m / 10;
        width = width + 1;
    }
    var i = width - 1;
    var v = n;
    while i >= 0 {
        out[at + i] = byte_of(48 + v % 10);
        v = v / 10;
        i = i - 1;
    }
    return at + width;
}

// Refuse a CONNECT with return code `code`: tell the client, then end the
// connection. No session is involved.
fn refuse_connect[&t, &c](tab: &!t conns.Table, core: &!c tables.Core, k: int, rule: int, code: int) -> [conn_write] int {
    region a {
        let b = alloc_slice[a](4, byte_of(0));
        let n = wire.put_connack(b, 0, 0, code);
        send_direct(tab, k, b[0..n]);
    }
    close_conn(tab, core, k, rule, false, true);
    return 1;
}

// The CONNECT in `pkt` on connection `k`. Answers 1 if the connection was closed.
fn handle_connect[&t, &c, &p](tab: &!t conns.Table, core: &!c tables.Core, k: int, pkt: &p [byte]) -> [conn_write] int {
    let ct = contents(core.ct);
    let r = wire.decode_connect(pkt, ct);
    if r < 0 {
        close_conn(tab, core, k, rules.of_wire(r), false, true);
        return 1;
    }
    if ct[0] != 4 {
        return refuse_connect(tab, core, k, rules.unsupported_level(), 1);
    }
    let flags = ct[1];
    let clean = flags >> 1 & 1;
    let will = flags >> 2 & 1;
    var idlen = ct[4];
    if idlen == 0 && clean == 0 {
        return refuse_connect(tab, core, k, rules.client_id(), 2);
    }
    if idlen > core.idmax {
        return refuse_connect(tab, core, k, rules.client_id(), 2);
    }
    if will == 1 && ct[6] + ct[8] > core.will_max {
        return refuse_connect(tab, core, k, rules.will_size(), 3);
    }
    let cd = contents(core.cs);
    let sd = contents(core.ss);
    var s = 0 - 1;
    var present = 0;
    region a {
        let auto = alloc_slice[a](core.idmax + 32, byte_of(0));
        var n = copy_into(auto, pkt[ct[3]..ct[3] + idlen]);
        if idlen == 0 {
            // An empty identifier with a clean session: the broker names it.
            core.auto_id = core.auto_id + 1;
            n = copy_into(auto, "auto-");
            n = digits(auto, n, core.auto_id);
        }
        let id = auto[0..n];
        var old = tables.find_session(core, id);
        if old >= 0 {
            let oc = sd[tables.s_stride() * old + 1];
            if oc >= 0 {
                // The same client again: the server closes the old connection, which
                // was not ended by a DISCONNECT, so its will is published (3.1.2-8) --
                // except when a persistent session is simply continued (the old one
                // and the new both ask for clean-session 0): the reconnecting client
                // is not gone, and Mosquitto does not announce it as gone either.
                tables.bump(core, tables.k_takeovers());
                let continued = sd[tables.s_stride() * old + 2] == 0 && clean == 0;
                close_conn(tab, core, oc, 0 - 1, !continued, false);
                old = tables.find_session(core, id);
            }
        }
        if old >= 0 {
            if clean == 1 {
                core.offline = core.offline - 1;
                tables.free_session(core, old);
            } else {
                core.offline = core.offline - 1;
                s = old;
                present = 1;
            }
        }
        if s < 0 {
            s = tables.new_session(core, id, clean, core.now);
        }
    }
    if s < 0 {
        return refuse_connect(tab, core, k, rules.client_id(), 3);
    }
    let sp = tables.s_stride() * s;
    let cp = tables.c_stride() * k;
    sd[sp + 1] = k;
    sd[sp + 2] = clean;
    sd[sp + 15] = core.now;
    sd[sp + 13] = 0;
    cd[cp + 3] = s;
    cd[cp + 4] = ct[2];
    core.online = core.online + 1;
    if will == 1 {
        let w = contents(core.wbuf);
        let base = s * core.will_max;
        let tl = ct[6];
        let ml = ct[8];
        copy_into(w[base..base + core.will_max], pkt[ct[5]..ct[5] + tl]);
        copy_into(w[base + tl..base + core.will_max], pkt[ct[7]..ct[7] + ml]);
        sd[sp + 13] = 1;
        sd[sp + 14] = flags >> 3 & 3;
        sd[sp + 16] = tl;
        sd[sp + 17] = ml;
        sd[sp + 18] = flags >> 5 & 1;
    }
    region a {
        let b = alloc_slice[a](4, byte_of(0));
        let n = wire.put_connack(b, 0, present, 0);
        if send_direct(tab, k, b[0..n]) < 0 {
            close_conn(tab, core, k, 0 - 1, true, true);
            return 1;
        }
    }
    tables.bump(core, tables.k_connects());
    if present == 1 {
        mark_dirty(core, k);
    }
    return 0;
}

// A PUBLISH from session `s` on connection `k`. Answers 1 if the connection was closed.
fn handle_publish[&t, &c, &p](tab: &!t conns.Table, core: &!c tables.Core, k: int, s: int, pkt: &p [byte]) -> [] int {
    let pt = contents(core.pt);
    let r = wire.decode_publish(pkt, pt);
    if r < 0 {
        close_conn(tab, core, k, rules.of_wire(r), true, true);
        return 1;
    }
    let qos = pt[5];
    if qos == 2 {
        close_conn(tab, core, k, rules.qos2_publish(), true, true);
        return 1;
    }
    let name = pkt[pt[0]..pt[0] + pt[1]];
    if !topic.valid_name(name, core.topic_max, core.levels_max) {
        close_conn(tab, core, k, rules.topic_invalid(), true, true);
        return 1;
    }
    let pid = pt[2];
    let retain = pt[6];
    route(core, name, pkt[pt[3]..pt[3] + pt[4]], qos, retain);
    if qos == 1 {
        if !enqueue_control(core, s, ctl_puback(), pid) {
            close_conn(tab, core, k, rules.output_full(), true, true);
            return 1;
        }
    }
    return 0;
}

fn handle_subscribe[&t, &c, &p](tab: &!t conns.Table, core: &!c tables.Core, k: int, s: int, pkt: &p [byte]) -> [] int {
    let ft = contents(core.ft);
    let codes = contents(core.codes);
    let r = wire.decode_filters(pkt, ft, true);
    if r < 0 {
        close_conn(tab, core, k, rules.of_wire(r), true, true);
        return 1;
    }
    let n = ft[1];
    var i = 0;
    while i < n {
        let f = pkt[ft[2 + 3 * i]..ft[2 + 3 * i] + ft[3 + 3 * i]];
        var code = 128;
        if !topic.valid_filter(f, core.topic_max, core.levels_max) {
            tables.refuse(core, rules.filter_invalid(), k, s, 0);
        } else {
            // QoS 2 is not offered: a request for it is granted QoS 1 (3.8.4).
            let got = subs.subscribe(core.trie, s, f, lower(ft[4 + 3 * i], 1));
            if got >= 0 {
                code = got;
            } else if got == subs.full_client() {
                tables.refuse(core, rules.subs_per_client(), k, s, 0);
            } else if got == subs.level_too_long() {
                tables.refuse(core, rules.topic_level(), k, s, 0);
            } else {
                tables.refuse(core, rules.subs_total(), k, s, 0);
            }
        }
        codes[i] = code;
        i = i + 1;
    }
    let size = wire.suback_size(n);
    let at = tables.q_reserve(core, s, size, false);
    if at < 0 {
        close_conn(tab, core, k, rules.output_full(), true, true);
        return 1;
    }
    wire.put_suback(tables.queue(core, s), at, ft[0], codes, n);
    tables.q_commit(core, s, at, size, 2, 0);
    mark_dirty(core, k);
    // Retained messages follow the SUBACK.
    i = 0;
    while i < n {
        if codes[i] < 128 {
            deliver_retained(core, s, pkt[ft[2 + 3 * i]..ft[2 + 3 * i] + ft[3 + 3 * i]], codes[i]);
        }
        i = i + 1;
    }
    return 0;
}

fn handle_unsubscribe[&t, &c, &p](tab: &!t conns.Table, core: &!c tables.Core, k: int, s: int, pkt: &p [byte]) -> [] int {
    let ft = contents(core.ft);
    let r = wire.decode_filters(pkt, ft, false);
    if r < 0 {
        close_conn(tab, core, k, rules.of_wire(r), true, true);
        return 1;
    }
    var i = 0;
    while i < ft[1] {
        let f = pkt[ft[2 + 3 * i]..ft[2 + 3 * i] + ft[3 + 3 * i]];
        if topic.valid_filter(f, core.topic_max, core.levels_max) {
            subs.unsubscribe(core.trie, s, f);
        }
        i = i + 1;
    }
    if !enqueue_control(core, s, ctl_unsuback(), ft[0]) {
        close_conn(tab, core, k, rules.output_full(), true, true);
        return 1;
    }
    return 0;
}

// One whole packet from connection `k`; answers 1 if the connection was closed.
fn handle_packet[&t, &c, &p](tab: &!t conns.Table, core: &!c tables.Core, k: int, pkt: &p [byte]) -> [conn_write] int {
    let cd = contents(core.cs);
    let s = cd[tables.c_stride() * k + 3];
    let kind = wire.kind(pkt);
    if s < 0 {
        // The first packet must be CONNECT (3.1.0-1).
        if kind != wire.t_connect() {
            close_conn(tab, core, k, rules.connect_first(), false, true);
            return 1;
        }
        let bad = wire.check_kind(kind, wire.flags(pkt));
        if bad < 0 {
            close_conn(tab, core, k, rules.of_wire(bad), false, true);
            return 1;
        }
        return handle_connect(tab, core, k, pkt);
    }
    if kind == wire.t_connect() {
        close_conn(tab, core, k, rules.connect_twice(), true, true);
        return 1;
    }
    let bad = wire.check_kind(kind, wire.flags(pkt));
    if bad < 0 {
        close_conn(tab, core, k, rules.of_wire(bad), true, true);
        return 1;
    }
    if kind == wire.t_publish() {
        return handle_publish(tab, core, k, s, pkt);
    }
    if kind == wire.t_subscribe() {
        return handle_subscribe(tab, core, k, s, pkt);
    }
    if kind == wire.t_unsubscribe() {
        return handle_unsubscribe(tab, core, k, s, pkt);
    }
    if kind == wire.t_puback() {
        let pid = wire.decode_puback(pkt);
        if pid < 0 {
            close_conn(tab, core, k, rules.of_wire(pid), true, true);
            return 1;
        }
        tables.q_ack(core, s, pid);
        // The window may have opened.
        mark_dirty(core, k);
        return 0;
    }
    if kind == wire.t_pingreq() {
        if wire.decode_empty(pkt) < 0 {
            close_conn(tab, core, k, rules.malformed(), true, true);
            return 1;
        }
        if !enqueue_control(core, s, ctl_pingresp(), 0) {
            close_conn(tab, core, k, rules.output_full(), true, true);
            return 1;
        }
        return 0;
    }
    // DISCONNECT: the will is discarded (3.14.4-3).
    if wire.decode_empty(pkt) < 0 {
        close_conn(tab, core, k, rules.malformed(), true, true);
        return 1;
    }
    close_conn(tab, core, k, 0 - 1, false, true);
    return 1;
}

// Handle every whole packet connection `k` has buffered, then slide what is left
// (the start of a packet still arriving) to the front of its buffer.
fn process_input[&t, &c](tab: &!t conns.Table, core: &!c tables.Core, k: int) -> [conn_write] int {
    let cd = contents(core.cs);
    let cp = tables.c_stride() * k;
    let base = k * core.pmax;
    let bf = contents(core.inbuf);
    var used = 0;
    var open = true;
    while open && used < cd[cp + 1] {
        let view = bf[base + used..base + cd[cp + 1]];
        let total = wire.header(view, core.pmax);
        if total == wire.incomplete() || total > len(view) {
            // The rest has not arrived yet.
            open = false;
        } else if total < 0 {
            close_conn(tab, core, k, rules.of_wire(total), true, true);
            return 1;
        } else {
            let before = cd[cp + 3];
            if handle_packet(tab, core, k, view[0..total]) == 1 {
                return 1;
            }
            used = used + total;
        }
    }
    if used > 0 {
        copy_within(bf[base..base + core.pmax], 0, used, cd[cp + 1] - used);
        cd[cp + 1] = cd[cp + 1] - used;
    }
    return 0;
}

// ---- the loop ------------------------------------------------------------

// One step of I/O on connection `k`, which the poller said was ready. `events` is
// 1 for readable, 2 for writable (a hang-up or error reads as readable).
fn step[&t, &c](tab: &!t conns.Table, core: &!c tables.Core, k: int, events: int, now: int) -> [conn_read, conn_write] int {
    let cd = contents(core.cs);
    let cp = tables.c_stride() * k;
    if events & 1 != 0 {
        let base = k * core.pmax;
        var code = 0;
        match conns.read(tab, k, contents(core.inbuf)[base + cd[cp + 1]..base + core.pmax]) {
            Received::Data(got) => {
                cd[cp + 1] = cd[cp + 1] + got;
                cd[cp + 2] = now;
                code = 1;
            }
            Received::End => {
                code = 2;
            }
            Received::Again => {
            }
            Received::Failed(e) => {
                code = 2;
            }
        }
        if code == 2 {
            close_conn(tab, core, k, 0 - 1, true, true);
            return 0;
        }
        if code == 1 {
            if process_input(tab, core, k) == 1 {
                return 0;
            }
        }
    }
    if events & 2 != 0 && cd[cp] == 1 {
        mark_dirty(core, k);
    }
    return 0;
}

// Take every connection waiting on the listener, up to the limit. Each goes in
// the table, is made non-blocking and is watched for input under the token
// `slot + 1` (the listener is token 0).
fn accept_all[&h, &l, &c](heap: &!h Heap, conn: conns.Table, listener: &!l Listener, core: &!c tables.Core, now: int) -> [heap, conn_accept, poll] conns.Table {
    let cd = contents(core.cs);
    var table = conn;
    var more = true;
    while more {
        match tcp_accept(listener) {
            Accepted::Ok(c) => {
                var held = 0;
                borrow table as &tt in {
                    held = conns.live(tt);
                }
                if held >= core.limit {
                    conn_close(c);
                    tables.refuse(core, rules.connections(), 0 - 1, 0 - 1, 0);
                } else {
                    let (grown, slot) = conns.put(heap, table, c);
                    table = grown;
                    if slot >= 0 {
                        let p = tables.c_stride() * slot;
                        cd[p] = 1;
                        cd[p + 1] = 0;
                        cd[p + 2] = now;
                        cd[p + 3] = 0 - 1;
                        cd[p + 4] = 0;
                        cd[p + 5] = 1;
                        cd[p + 6] = 0;
                        cd[p + 7] = now;
                        cd[p + 8] = 0;
                        cd[p + 9] = 0;
                        cd[p + 10] = 0;
                        cd[p + 11] = cd[p + 11] + 1;
                        borrow mut table as &!ct in {
                            if conns.nonblocking(ct, slot) != 0 || conns.nodelay(ct, slot) != 0 || conns.watch(ct, core.poller, slot, slot + 1, 1) != 0 {
                                shut(ct, core, slot);
                            }
                        }
                    }
                }
            }
            Accepted::Again => {
                more = false;
            }
            Accepted::Failed(e) => {
                more = false;
            }
        }
    }
    return table;
}

// Once a second: connections that have not sent CONNECT in time, that have been
// silent for one and a half keepalives, or that cannot be written to.
fn sweep[&t, &c](tab: &!t conns.Table, core: &!c tables.Core, now: int) -> [] int {
    let cd = contents(core.cs);
    var k = 0;
    while k < conns.slots(tab) {
        let p = tables.c_stride() * k;
        if cd[p] == 1 {
            if cd[p + 3] < 0 {
                if now - cd[p + 7] > core.connect_to {
                    close_conn(tab, core, k, rules.connect_timeout(), false, true);
                }
            } else if cd[p + 4] > 0 && now - cd[p + 2] > cd[p + 4] + cd[p + 4] / 2 {
                close_conn(tab, core, k, rules.keepalive(), true, true);
            } else if cd[p + 9] > 0 && now - cd[p + 9] > core.stall_to {
                close_conn(tab, core, k, rules.write_stalled(), true, true);
            }
        }
        k = k + 1;
    }
    return 0;
}

// Wait for the network, at most `timeout_ms`, and do the I/O that is ready: take
// new connections, read and handle packets, flush what was queued, close the ones
// that have gone quiet. By value, because accepting replaces the connection table.
pub fn wait[&h, &k, &l](heap: &!h Heap, b: Broker, clock: &k Clock, listener: &!l Listener, timeout_ms: int) -> [heap, conn_accept, conn_read, conn_write, poll, clock] Broker {
    let Broker { tab, core } = b;
    var table = tab;
    var state = core;
    var ready = 0 - 1;
    borrow mut state as &!cw in {
        cw.nforeign = 0;
        ready = poller_wait(cw.poller, contents(cw.events), timeout_ms);
    }
    let ms = clock_ms(clock);
    let now = ms / 1000;
    borrow mut state as &!cw in {
        if cw.start_ms == 0 {
            cw.start_ms = ms;
        }
        cw.now = now;
        cw.now_ms = ms - cw.start_ms;
    }
    if ready >= 0 {
        // New connections first, while the table is ours to grow.
        var j = 0;
        while j < ready {
            var token = 0 - 1;
            borrow state as &cr in {
                token = contents(cr.events)[2 * j];
            }
            if token == 0 {
                borrow mut state as &!cw in {
                    table = accept_all(heap, table, listener, cw, now);
                }
            }
            j = j + 1;
        }
        borrow mut table as &!tw in {
            borrow mut state as &!cw in {
                j = 0;
                while j < ready {
                    let token = contents(cw.events)[2 * j];
                    let ev = contents(cw.events)[2 * j + 1];
                    if token > cw.limit {
                        if 2 * cw.nforeign + 1 < len(contents(cw.foreign)) {
                            contents(cw.foreign)[2 * cw.nforeign] = token;
                            contents(cw.foreign)[2 * cw.nforeign + 1] = ev;
                            cw.nforeign = cw.nforeign + 1;
                        }
                    } else if token > 0 && contents(cw.cs)[tables.c_stride() * (token - 1)] == 1 {
                        step(tw, cw, token - 1, ev, now);
                    }
                    j = j + 1;
                }
            }
        }
    }
    borrow mut table as &!tw in {
        borrow mut state as &!cw in {
            flush_dirty(tw, cw);
            if now != cw.last_sweep {
                cw.last_sweep = now;
                sweep(tw, cw, now);
                flush_dirty(tw, cw);
            }
        }
    }
    return Broker { tab: table, core: state };
}

// The poller, to register handles that are not connections (the signal watch).
pub fn poller[&b](b: &!b Broker) -> [] &!b Poller {
    return b.core.poller;
}

// The first token the application may register a handle under.
pub fn first_token[&b](b: &b Broker) -> [] int {
    return b.core.limit + 1;
}

pub fn foreign_count[&b](b: &b Broker) -> [] int {
    return b.core.nforeign;
}

pub fn foreign[&b](b: &b Broker) -> [] &b [int] {
    return contents(b.core.foreign)[0..2 * b.core.nforeign];
}

// ---- what the log reads --------------------------------------------------

pub fn counter[&b](b: &b Broker, i: int) -> [] int {
    return tables.counter(b.core, i);
}

pub fn online[&b](b: &b Broker) -> [] int {
    return b.core.online;
}

pub fn offline[&b](b: &b Broker) -> [] int {
    return b.core.offline;
}

pub fn subscriptions[&b](b: &b Broker) -> [] int {
    return subs.count(b.core.trie);
}

pub fn retained[&b](b: &b Broker) -> [] int {
    return b.core.retained_used;
}

pub fn events_waiting[&b](b: &b Broker) -> [] int {
    return tables.events_waiting(b.core);
}

pub fn event_field[&b](b: &b Broker, field: int) -> [] int {
    return tables.event_field(b.core, field);
}

pub fn event_id[&b](b: &b Broker) -> [] &b [byte] {
    return tables.event_id(b.core);
}

pub fn event_pop[&b](b: &!b Broker) -> [] int {
    return tables.event_pop(b.core);
}
