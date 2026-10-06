edition 5;

// `mqtt.subs` -- the subscription trie (design section 9, issue #4).
//
// A trie by topic level, in fixed tables sized at start: a node per distinct
// filter prefix, found from its parent and label through a hash index, and a
// subscription record per (session, filter) chained both on its node and on its
// session. Nothing is allocated after `open`; when a table is full the answer is
// a code (`subscribe` refuses, with the rule's number) and nothing is changed.
//
// Matching a topic name walks the trie with an explicit stack, collecting each
// session once at the highest QoS any of its filters grants (design section 9),
// into output arrays the caller reads with `matched_*`.

module mqtt.subs;

import mqtt.topic;

// Longest level a subscription may have; a longer one is refused at SUBSCRIBE
// (`limit.topic-level`). A PUBLISH with a longer level simply matches no node.
pub fn label_max() -> [] int {
    return 64;
}

// Node fields.
fn n_stride() -> [] int {
    return 10;
}

// 0 parent, 1 label length, 2 next in hash chain, 3 first subscription, 4 children,
// 5 in use, 6 hash, 7 next free, 8 the `+` child, 9 the `#` child.

// Subscription fields.
fn s_stride() -> [] int {
    return 6;
}

// 0 node, 1 session, 2 qos, 3 next on node, 4 previous on node, 5 next on session.

// `subscribe` answers: the granted QoS (0 to 2), or one of these.
pub fn full_total() -> [] int {
    return 0 - 1;
}

pub fn full_client() -> [] int {
    return 0 - 2;
}

pub fn full_nodes() -> [] int {
    return 0 - 3;
}

pub fn level_too_long() -> [] int {
    return 0 - 4;
}

pub res struct Subs {
    nodes: Box[[int]],
    labels: Box[[byte]],
    buckets: Box[[int]],
    subs: Box[[int]],
    sess_head: Box[[int]],
    sess_count: Box[[int]],
    seen: Box[[int]],
    slot_of: Box[[int]],
    out_sess: Box[[int]],
    out_qos: Box[[int]],
    stack: Box[[int]],
    max_nodes: int,
    max_subs: int,
    max_sessions: int,
    max_levels: int,
    per_client: int,
    node_top: int,
    node_free: int,
    sub_top: int,
    sub_free: int,
    nsubs: int,
    epoch: int,
    nmatched: int,
}

// A trie for `max_sessions` sessions with room for `max_subs` subscriptions in
// all and `per_client` for one session, over names of at most `max_levels` levels.
// The node table is `max_nodes` (one is the root).
pub fn open[&h](heap: &!h Heap, max_nodes: int, max_subs: int, max_sessions: int, max_levels: int, per_client: int) -> [heap] Subs {
    let buckets = 2 * max_nodes + 1;
    var nodes = box_slice(heap, n_stride() * max_nodes, 0);
    // The root.
    borrow mut nodes as &!nb in {
        let nd = contents(nb);
        nd[3] = 0 - 1;
        nd[5] = 1;
        nd[8] = 0 - 1;
        nd[9] = 0 - 1;
    }
    return Subs { nodes: nodes, labels: box_slice(heap, label_max() * max_nodes, byte_of(0)), buckets: box_slice(heap, buckets, 0 - 1), subs: box_slice(heap, s_stride() * max_subs, 0), sess_head: box_slice(heap, max_sessions, 0 - 1), sess_count: box_slice(heap, max_sessions, 0), seen: box_slice(heap, max_sessions, 0), slot_of: box_slice(heap, max_sessions, 0), out_sess: box_slice(heap, max_sessions, 0), out_qos: box_slice(heap, max_sessions, 0), stack: box_slice(heap, 4 * (max_levels + 4), 0), max_nodes: max_nodes, max_subs: max_subs, max_sessions: max_sessions, max_levels: max_levels, per_client: per_client, node_top: 1, node_free: 0 - 1, sub_top: 0, sub_free: 0 - 1, nsubs: 0, epoch: 0, nmatched: 0 };
}

pub fn drop[&h](heap: &!h Heap, s: Subs) -> [heap] int {
    let Subs { nodes, labels, buckets, subs, sess_head, sess_count, seen, slot_of, out_sess, out_qos, stack, max_nodes, max_subs, max_sessions, max_levels, per_client, node_top, node_free, sub_top, sub_free, nsubs, epoch, nmatched } = s;
    unbox_slice(heap, nodes);
    unbox_slice(heap, labels);
    unbox_slice(heap, buckets);
    unbox_slice(heap, subs);
    unbox_slice(heap, sess_head);
    unbox_slice(heap, sess_count);
    unbox_slice(heap, seen);
    unbox_slice(heap, slot_of);
    unbox_slice(heap, out_sess);
    unbox_slice(heap, out_qos);
    unbox_slice(heap, stack);
    return 0;
}

// How many subscriptions are held, and how many nodes are in use.
pub fn count[&s](s: &s Subs) -> [] int {
    return s.nsubs;
}

pub fn nodes_in_use[&s](s: &s Subs) -> [] int {
    var n = s.node_top;
    var f = s.node_free;
    let nd = contents(s.nodes);
    while f >= 0 {
        n = n - 1;
        f = nd[n_stride() * f + 7];
    }
    return n;
}

pub fn session_count[&s](s: &s Subs, session: int) -> [] int {
    return contents(s.sess_count)[session];
}

// ---- nodes ---------------------------------------------------------------

// A hash of (parent, label) small enough that nothing overflows.
fn hash_of[&t](parent: int, text: &t [byte]) -> [] int {
    var h = (parent + 7) % 1000003;
    var i = 0;
    while i < len(text) {
        h = (h * 131 + int_of(text[i]) + 1) % 1000000007;
        i = i + 1;
    }
    return h;
}

fn label_is[&s, &t](s: &s Subs, node: int, text: &t [byte]) -> [] bool {
    let nd = contents(s.nodes);
    if nd[n_stride() * node + 1] != len(text) {
        return false;
    }
    let lb = contents(s.labels);
    var i = 0;
    while i < len(text) {
        if int_of(lb[node * label_max() + i]) != int_of(text[i]) {
            return false;
        }
        i = i + 1;
    }
    return true;
}

// The child of `parent` labelled `text`, or -1.
fn find_child[&s, &t](s: &s Subs, parent: int, text: &t [byte]) -> [] int {
    if len(text) > label_max() {
        return 0 - 1;
    }
    let nd = contents(s.nodes);
    let h = hash_of(parent, text);
    var node = contents(s.buckets)[h % len(contents(s.buckets))];
    while node >= 0 {
        let p = n_stride() * node;
        if nd[p] == parent && nd[p + 6] == h && label_is(s, node, text) {
            return node;
        }
        node = nd[p + 2];
    }
    return 0 - 1;
}

// Take a node from the free list or the unused end of the table; -1 if neither.
fn take_node[&s](s: &!s Subs) -> [] int {
    let nd = contents(s.nodes);
    if s.node_free >= 0 {
        let node = s.node_free;
        s.node_free = nd[n_stride() * node + 7];
        return node;
    }
    if s.node_top < s.max_nodes {
        let node = s.node_top;
        s.node_top = s.node_top + 1;
        return node;
    }
    return 0 - 1;
}

// A new child of `parent` labelled `text` (not already there); -1 if the node
// table is full.
fn make_child[&s, &t](s: &!s Subs, parent: int, text: &t [byte]) -> [] int {
    let node = take_node(s);
    if node < 0 {
        return 0 - 1;
    }
    let nd = contents(s.nodes);
    let lb = contents(s.labels);
    let p = n_stride() * node;
    let h = hash_of(parent, text);
    let bk = contents(s.buckets);
    let b = h % len(bk);
    nd[p] = parent;
    nd[p + 1] = len(text);
    nd[p + 2] = bk[b];
    nd[p + 3] = 0 - 1;
    nd[p + 4] = 0;
    nd[p + 5] = 1;
    nd[p + 6] = h;
    nd[p + 7] = 0 - 1;
    nd[p + 8] = 0 - 1;
    nd[p + 9] = 0 - 1;
    copy_into(lb[node * label_max()..(node + 1) * label_max()], text);
    bk[b] = node;
    let q = n_stride() * parent;
    nd[q + 4] = nd[q + 4] + 1;
    if len(text) == 1 && int_of(text[0]) == 43 {
        nd[q + 8] = node;
    }
    if len(text) == 1 && int_of(text[0]) == 35 {
        nd[q + 9] = node;
    }
    return node;
}

// Give back `node` and then any ancestors left with nothing in them.
fn prune[&s](s: &!s Subs, start: int) -> [] int {
    let nd = contents(s.nodes);
    let bk = contents(s.buckets);
    var node = start;
    while node > 0 && nd[n_stride() * node + 3] < 0 && nd[n_stride() * node + 4] == 0 {
        let p = n_stride() * node;
        let parent = nd[p];
        // Out of its hash chain.
        let b = nd[p + 6] % len(bk);
        if bk[b] == node {
            bk[b] = nd[p + 2];
        } else {
            var at = bk[b];
            while at >= 0 && nd[n_stride() * at + 2] != node {
                at = nd[n_stride() * at + 2];
            }
            if at >= 0 {
                nd[n_stride() * at + 2] = nd[p + 2];
            }
        }
        let q = n_stride() * parent;
        nd[q + 4] = nd[q + 4] - 1;
        if nd[q + 8] == node {
            nd[q + 8] = 0 - 1;
        }
        if nd[q + 9] == node {
            nd[q + 9] = 0 - 1;
        }
        nd[p + 5] = 0;
        nd[p + 7] = s.node_free;
        s.node_free = node;
        node = parent;
    }
    return 0;
}

// The node of `filter`, or -1 if it is not in the trie.
fn find_filter[&s, &f](s: &s Subs, filter: &f [byte]) -> [] int {
    var node = 0;
    var at = 0;
    var going = true;
    while going {
        let end = topic.level_end(filter, at);
        node = find_child(s, node, filter[at..end]);
        if node < 0 {
            return 0 - 1;
        }
        if end >= len(filter) {
            going = false;
        } else {
            at = end + 1;
        }
    }
    return node;
}

// The node of `filter`, creating what is missing. Answers `full_nodes` or
// `level_too_long`, leaving nothing it created behind.
fn make_filter[&s, &f](s: &!s Subs, filter: &f [byte]) -> [] int {
    var node = 0;
    var at = 0;
    var going = true;
    while going {
        let end = topic.level_end(filter, at);
        if end - at > label_max() {
            prune(s, node);
            return level_too_long();
        }
        var child = find_child(s, node, filter[at..end]);
        if child < 0 {
            child = make_child(s, node, filter[at..end]);
            if child < 0 {
                prune(s, node);
                return full_nodes();
            }
        }
        node = child;
        if end >= len(filter) {
            going = false;
        } else {
            at = end + 1;
        }
    }
    return node;
}

// ---- subscriptions -------------------------------------------------------

fn take_sub[&s](s: &!s Subs) -> [] int {
    let sb = contents(s.subs);
    if s.sub_free >= 0 {
        let r = s.sub_free;
        s.sub_free = sb[s_stride() * r + 3];
        return r;
    }
    if s.sub_top < s.max_subs {
        let r = s.sub_top;
        s.sub_top = s.sub_top + 1;
        return r;
    }
    return 0 - 1;
}

// Take `rec` off its node's chain and give the record back. Does not touch the
// session's chain.
fn unlink_from_node[&s](s: &!s Subs, rec: int) -> [] int {
    let sb = contents(s.subs);
    let nd = contents(s.nodes);
    let p = s_stride() * rec;
    let node = sb[p];
    let next = sb[p + 3];
    let prev = sb[p + 4];
    if prev >= 0 {
        sb[s_stride() * prev + 3] = next;
    } else {
        nd[n_stride() * node + 3] = next;
    }
    if next >= 0 {
        sb[s_stride() * next + 4] = prev;
    }
    return 0;
}

fn free_sub[&s](s: &!s Subs, rec: int) -> [] int {
    let sb = contents(s.subs);
    sb[s_stride() * rec + 3] = s.sub_free;
    s.sub_free = rec;
    s.nsubs = s.nsubs - 1;
    return 0;
}

// The record of `session` on `node`, or -1.
fn find_sub[&s](s: &s Subs, node: int, session: int) -> [] int {
    let sb = contents(s.subs);
    var r = contents(s.nodes)[n_stride() * node + 3];
    while r >= 0 {
        if sb[s_stride() * r + 1] == session {
            return r;
        }
        r = sb[s_stride() * r + 3];
    }
    return 0 - 1;
}

// Subscribe `session` to `filter` at `qos` (0 to 2), or change the QoS of a
// subscription it already has. Answers the QoS granted, or `full_client`,
// `full_total`, `full_nodes`, `level_too_long`; a refusal changes nothing. The
// filter is assumed valid (`topic.valid_filter`).
pub fn subscribe[&s, &f](s: &!s Subs, session: int, filter: &f [byte], qos: int) -> [] int {
    let existing = find_filter(s, filter);
    if existing >= 0 {
        let r = find_sub(s, existing, session);
        if r >= 0 {
            contents(s.subs)[s_stride() * r + 2] = qos;
            return qos;
        }
    }
    if contents(s.sess_count)[session] >= s.per_client {
        return full_client();
    }
    if s.nsubs >= s.max_subs {
        return full_total();
    }
    let node = make_filter(s, filter);
    if node < 0 {
        return node;
    }
    let rec = take_sub(s);
    if rec < 0 {
        prune(s, node);
        return full_total();
    }
    let sb = contents(s.subs);
    let nd = contents(s.nodes);
    let p = s_stride() * rec;
    let head = nd[n_stride() * node + 3];
    sb[p] = node;
    sb[p + 1] = session;
    sb[p + 2] = qos;
    sb[p + 3] = head;
    sb[p + 4] = 0 - 1;
    if head >= 0 {
        sb[s_stride() * head + 4] = rec;
    }
    nd[n_stride() * node + 3] = rec;
    let sh = contents(s.sess_head);
    sb[p + 5] = sh[session];
    sh[session] = rec;
    contents(s.sess_count)[session] = contents(s.sess_count)[session] + 1;
    s.nsubs = s.nsubs + 1;
    return qos;
}

// Remove `session`'s subscription to `filter`. True if there was one.
pub fn unsubscribe[&s, &f](s: &!s Subs, session: int, filter: &f [byte]) -> [] bool {
    let node = find_filter(s, filter);
    if node < 0 {
        return false;
    }
    let rec = find_sub(s, node, session);
    if rec < 0 {
        return false;
    }
    unlink_from_node(s, rec);
    // Out of the session's chain.
    let sb = contents(s.subs);
    let sh = contents(s.sess_head);
    if sh[session] == rec {
        sh[session] = sb[s_stride() * rec + 5];
    } else {
        var at = sh[session];
        while at >= 0 && sb[s_stride() * at + 5] != rec {
            at = sb[s_stride() * at + 5];
        }
        if at >= 0 {
            sb[s_stride() * at + 5] = sb[s_stride() * rec + 5];
        }
    }
    contents(s.sess_count)[session] = contents(s.sess_count)[session] - 1;
    free_sub(s, rec);
    prune(s, node);
    return true;
}

// Remove every subscription of `session`.
pub fn remove_session[&s](s: &!s Subs, session: int) -> [] int {
    let sb = contents(s.subs);
    let sh = contents(s.sess_head);
    var rec = sh[session];
    var removed = 0;
    while rec >= 0 {
        let next = sb[s_stride() * rec + 5];
        let node = sb[s_stride() * rec];
        unlink_from_node(s, rec);
        free_sub(s, rec);
        prune(s, node);
        removed = removed + 1;
        rec = next;
    }
    sh[session] = 0 - 1;
    contents(s.sess_count)[session] = 0;
    return removed;
}

// ---- matching ------------------------------------------------------------

// Add every session subscribed on `node`, once, at the highest QoS seen.
fn collect[&s](s: &!s Subs, node: int) -> [] int {
    let sb = contents(s.subs);
    let seen = contents(s.seen);
    let slot = contents(s.slot_of);
    let os = contents(s.out_sess);
    let oq = contents(s.out_qos);
    var r = contents(s.nodes)[n_stride() * node + 3];
    while r >= 0 {
        let session = sb[s_stride() * r + 1];
        let q = sb[s_stride() * r + 2];
        if seen[session] == s.epoch {
            if q > oq[slot[session]] {
                oq[slot[session]] = q;
            }
        } else {
            seen[session] = s.epoch;
            slot[session] = s.nmatched;
            os[s.nmatched] = session;
            oq[s.nmatched] = q;
            s.nmatched = s.nmatched + 1;
        }
        r = sb[s_stride() * r + 3];
    }
    return 0;
}

// Find the sessions subscribed to a filter that matches `name`: how many, then
// `matched_session(i)` and `matched_qos(i)`. `name` is assumed valid.
pub fn find_matches[&s, &n](s: &!s Subs, name: &n [byte]) -> [] int {
    s.epoch = s.epoch % 2000000000 + 1;
    s.nmatched = 0;
    let nd = contents(s.nodes);
    let st = contents(s.stack);
    let dollar = len(name) > 0 && int_of(name[0]) == 36;
    var sp = 0;
    st[0] = 0;
    st[1] = 0;
    sp = 1;
    while sp > 0 {
        sp = sp - 1;
        let node = st[2 * sp];
        let pos = st[2 * sp + 1];
        let p = n_stride() * node;
        // `#` matches whatever is left, including nothing, except that a
        // leading wildcard never matches a `$` name.
        let hash = nd[p + 9];
        if hash >= 0 && !(dollar && node == 0) {
            collect(s, hash);
        }
        if pos > len(name) {
            collect(s, node);
        } else {
            let end = topic.level_end(name, pos);
            let next = end + 1;
            let lit = find_child(s, node, name[pos..end]);
            if lit >= 0 && 2 * sp + 3 < len(st) {
                st[2 * sp] = lit;
                st[2 * sp + 1] = next;
                sp = sp + 1;
            }
            let plus = nd[p + 8];
            if plus >= 0 && !(dollar && node == 0) && 2 * sp + 3 < len(st) {
                st[2 * sp] = plus;
                st[2 * sp + 1] = next;
                sp = sp + 1;
            }
        }
    }
    return s.nmatched;
}

pub fn matched_session[&s](s: &s Subs, i: int) -> [] int {
    return contents(s.out_sess)[i];
}

pub fn matched_qos[&s](s: &s Subs, i: int) -> [] int {
    return contents(s.out_qos)[i];
}
