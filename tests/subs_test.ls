edition 5;

import std.test;
import mqtt.subs;
import mqtt.topic;

// Level `i` of the test alphabet. Filters draw on all of it; names on the first
// three and `$s` (no wildcards in a name).
fn level_text(i: int) -> [] &static [byte] {
    if i == 0 {
        return "a";
    }
    if i == 1 {
        return "b";
    }
    if i == 2 {
        return "";
    }
    if i == 3 {
        return "+";
    }
    if i == 4 {
        return "#";
    }
    return "$s";
}

// The text of code `code` with `n` levels over `base` symbols, into `out`;
// answers its length. `names` picks the name alphabet (a, b, empty, $s).
fn build[&o](out: &!o [byte], code: int, n: int, base: int, names: bool) -> [] int {
    var c = code;
    var at = 0;
    var k = 0;
    while k < n {
        var sym = c % base;
        c = c / base;
        if names && sym == 3 {
            sym = 5;
        }
        let t = level_text(sym);
        if k > 0 {
            out[at] = byte_of(47);
            at = at + 1;
        }
        at = at + copy_into(out[at..len(out)], t);
        k = k + 1;
    }
    return at;
}

fn pow(base: int, n: int) -> [] int {
    var r = 1;
    var i = 0;
    while i < n {
        r = r * base;
        i = i + 1;
    }
    return r;
}

// Every valid filter of one to three levels against every name of one to three
// levels: the trie answers as `topic.matches` does.
pub fn test_trie_agrees_with_the_reference_for_every_single_filter[&h](heap: &!h Heap) -> [heap] int {
    var s = subs.open(heap, 64, 64, 4, 8, 8);
    region a {
        let f = alloc_slice[a](32, byte_of(0));
        let nm = alloc_slice[a](32, byte_of(0));
        var fn_ = 1;
        while fn_ <= 3 {
            var fc = 0;
            while fc < pow(6, fn_) {
                let fl = build(f, fc, fn_, 6, false);
                if topic.valid_filter(f[0..fl], 100, 8) {
                    borrow mut s as &!sw in {
                        test.assert_eq(subs.subscribe(sw, 0, f[0..fl], 1), 1);
                    }
                    var nn = 1;
                    while nn <= 3 {
                        var nc = 0;
                        while nc < pow(4, nn) {
                            let nl = build(nm, nc, nn, 4, true);
                            var got = 0;
                            borrow mut s as &!sw in {
                                got = subs.find_matches(sw, nm[0..nl]);
                            }
                            var want = 0;
                            if topic.matches(f[0..fl], nm[0..nl]) {
                                want = 1;
                            }
                            test.assert_eq(got, want);
                            nc = nc + 1;
                        }
                        nn = nn + 1;
                    }
                    borrow mut s as &!sw in {
                        test.assert(subs.unsubscribe(sw, 0, f[0..fl]));
                        // Nothing is left behind.
                        test.assert_eq(subs.count(sw), 0);
                    }
                    borrow s as &sr in {
                        test.assert_eq(subs.nodes_in_use(sr), 1);
                    }
                }
                fc = fc + 1;
            }
            fn_ = fn_ + 1;
        }
    }
    subs.drop(heap, s);
    return 0;
}

// Many sessions at once, with overlapping filters: each matching session once,
// at the highest QoS of its matching filters.
pub fn test_overlap_dedupes_at_the_highest_qos[&h](heap: &!h Heap) -> [heap] int {
    var s = subs.open(heap, 64, 64, 8, 8, 8);
    borrow mut s as &!sw in {
        test.assert_eq(subs.subscribe(sw, 1, "a/b", 0), 0);
        test.assert_eq(subs.subscribe(sw, 1, "a/+", 1), 1);
        test.assert_eq(subs.subscribe(sw, 1, "#", 0), 0);
        test.assert_eq(subs.subscribe(sw, 2, "a/b", 1), 1);
        test.assert_eq(subs.subscribe(sw, 3, "x/y", 1), 1);
        test.assert_eq(subs.find_matches(sw, "a/b"), 2);
        var q1 = 0 - 1;
        var q2 = 0 - 1;
        var i = 0;
        while i < 2 {
            if subs.matched_session(sw, i) == 1 {
                q1 = subs.matched_qos(sw, i);
            }
            if subs.matched_session(sw, i) == 2 {
                q2 = subs.matched_qos(sw, i);
            }
            i = i + 1;
        }
        test.assert_eq(q1, 1);
        test.assert_eq(q2, 1);
        test.assert_eq(subs.find_matches(sw, "x/y"), 2);
        test.assert_eq(subs.find_matches(sw, "q"), 1);
        // Changing a QoS replaces it and adds no subscription.
        test.assert_eq(subs.count(sw), 5);
        test.assert_eq(subs.subscribe(sw, 1, "a/+", 0), 0);
        test.assert_eq(subs.count(sw), 5);
        // Dropping a session removes all of its subscriptions.
        test.assert_eq(subs.remove_session(sw, 1), 3);
        test.assert_eq(subs.count(sw), 2);
        test.assert_eq(subs.find_matches(sw, "a/b"), 1);
        test.assert(!subs.unsubscribe(sw, 1, "#"));
        test.assert(subs.unsubscribe(sw, 2, "a/b"));
        test.assert(subs.unsubscribe(sw, 3, "x/y"));
        test.assert_eq(subs.count(sw), 0);
    }
    borrow s as &sr in {
        test.assert_eq(subs.nodes_in_use(sr), 1);
    }
    subs.drop(heap, s);
    return 0;
}

pub fn test_bounds_are_refused_with_their_codes[&h](heap: &!h Heap) -> [heap] int {
    // Two per client, four in all, six nodes.
    var s = subs.open(heap, 6, 4, 4, 8, 2);
    borrow mut s as &!sw in {
        test.assert_eq(subs.subscribe(sw, 0, "a", 0), 0);
        test.assert_eq(subs.subscribe(sw, 0, "b", 0), 0);
        test.assert_eq(subs.subscribe(sw, 0, "c", 0), subs.full_client());
        // A second subscription to a filter the session has is a QoS change, not a new one.
        test.assert_eq(subs.subscribe(sw, 0, "a", 2), 2);
        test.assert_eq(subs.subscribe(sw, 1, "a", 0), 0);
        test.assert_eq(subs.subscribe(sw, 1, "b", 0), 0);
        test.assert_eq(subs.subscribe(sw, 2, "b", 0), subs.full_total());
        // Nothing changed by refusals.
        test.assert_eq(subs.count(sw), 4);
        test.assert_eq(subs.session_count(sw, 2), 0);
        test.assert(subs.unsubscribe(sw, 1, "b"));
        test.assert_eq(subs.subscribe(sw, 2, "b", 1), 1);
    }
    // Node exhaustion: the root and five more.
    var t = subs.open(heap, 6, 20, 4, 8, 20);
    borrow mut t as &!tw in {
        test.assert_eq(subs.subscribe(tw, 0, "a/b/c/d/e", 0), 0);
        // One more level needs a sixth node besides the root: refused, and what
        // the attempt created is given back.
        test.assert_eq(subs.subscribe(tw, 1, "x/y", 0), subs.full_nodes());
        test.assert_eq(subs.count(tw), 1);
    }
    borrow t as &tr in {
        test.assert_eq(subs.nodes_in_use(tr), 6);
    }
    // A level longer than a label.
    region a {
        let long = alloc_slice[a](100, byte_of(97));
        borrow mut t as &!tw in {
            test.assert_eq(subs.subscribe(tw, 2, long[0..subs.label_max() + 1], 0), subs.level_too_long());
            test.assert_eq(subs.subscribe(tw, 2, long[0..subs.label_max()], 0), subs.full_nodes());
        }
    }
    subs.drop(heap, s);
    subs.drop(heap, t);
    return 0;
}

// Subscribe and unsubscribe in a pattern that recycles nodes and records, many
// times over: the tables never leak.
pub fn test_churn_does_not_leak[&h](heap: &!h Heap) -> [heap] int {
    var s = subs.open(heap, 32, 32, 4, 8, 8);
    var round = 0;
    while round < 500 {
        borrow mut s as &!sw in {
            test.assert_eq(subs.subscribe(sw, round % 4, "k/+/z", round % 3), round % 3);
            test.assert_eq(subs.subscribe(sw, round % 4, "k/#", 0), 0);
            test.assert_eq(subs.subscribe(sw, (round + 1) % 4, "q/r/s/t", 1), 1);
            test.assert(subs.find_matches(sw, "k/m/z") >= 1);
            subs.remove_session(sw, round % 4);
            subs.remove_session(sw, (round + 1) % 4);
            test.assert_eq(subs.count(sw), 0);
        }
        round = round + 1;
    }
    borrow s as &sr in {
        test.assert_eq(subs.nodes_in_use(sr), 1);
    }
    subs.drop(heap, s);
    return 0;
}
