edition 5;

// `mqtt.topic` -- topic names and filters (MQTT 3.1.1 section 4.7, issue #4).
//
// `valid_name` and `valid_filter` apply the wildcard rules and the broker's
// bounds; `matches` is the specification's matching written plainly, level by
// level, and is the reference the subscription trie is tested against and the
// matcher used for retained messages. None of them trap on any input.

module mqtt.topic;

// 47 is '/', 43 is '+', 35 is '#', 36 is '$'.

// The end of the level that starts at `at`: the index of the next '/', or `len`.
pub fn level_end[&t](text: &t [byte], at: int) -> [] int {
    var i = at;
    while i < len(text) && int_of(text[i]) != 47 {
        i = i + 1;
    }
    return i;
}

// How many levels `text` has: one more than its '/' count.
pub fn levels[&t](text: &t [byte]) -> [] int {
    var n = 1;
    var i = 0;
    while i < len(text) {
        if int_of(text[i]) == 47 {
            n = n + 1;
        }
        i = i + 1;
    }
    return n;
}

// A topic name (what a PUBLISH carries): not empty, within `max_len` bytes and
// `max_levels` levels, and with no wildcard character in it (4.7.1, 3.3.2-2).
pub fn valid_name[&t](text: &t [byte], max_len: int, max_levels: int) -> [] bool {
    if len(text) == 0 || len(text) > max_len {
        return false;
    }
    var i = 0;
    while i < len(text) {
        let c = int_of(text[i]);
        if c == 43 || c == 35 {
            return false;
        }
        i = i + 1;
    }
    return levels(text) <= max_levels;
}

// A topic filter: not empty, within the bounds, `+` only as a whole level and `#`
// only as the whole last level (4.7.1.2, 4.7.1.3).
pub fn valid_filter[&t](text: &t [byte], max_len: int, max_levels: int) -> [] bool {
    if len(text) == 0 || len(text) > max_len || levels(text) > max_levels {
        return false;
    }
    var at = 0;
    var going = true;
    while going {
        let end = level_end(text, at);
        var i = at;
        var wild = 0;
        while i < end {
            let c = int_of(text[i]);
            if c == 43 || c == 35 {
                wild = wild + 1;
            }
            i = i + 1;
        }
        if wild > 0 {
            // A wildcard must be the whole level.
            if end - at != 1 {
                return false;
            }
            // `#` must be the last level.
            if int_of(text[at]) == 35 && end != len(text) {
                return false;
            }
        }
        if end >= len(text) {
            going = false;
        } else {
            at = end + 1;
        }
    }
    return true;
}

// Does `filter` match the topic name `name`? Both are assumed valid.
//
// The specification's rules, level by level: a literal level matches itself, `+`
// matches exactly one level, `#` matches the rest (and the parent: `a/#` matches
// `a`); a name beginning with `$` is matched by no filter whose first level is a
// wildcard (4.7.2-1).
pub fn matches[&f, &n](filter: &f [byte], name: &n [byte]) -> [] bool {
    if len(name) > 0 && int_of(name[0]) == 36 {
        if len(filter) > 0 && (int_of(filter[0]) == 43 || int_of(filter[0]) == 35) {
            return false;
        }
    }
    var fa = 0;
    var na = 0;
    while true {
        let fend = level_end(filter, fa);
        let flen = fend - fa;
        if flen == 1 && int_of(filter[fa]) == 35 {
            return true;
        }
        let nend = level_end(name, na);
        let nlen = nend - na;
        if !(flen == 1 && int_of(filter[fa]) == 43) {
            if flen != nlen {
                return false;
            }
            var i = 0;
            while i < flen {
                if int_of(filter[fa + i]) != int_of(name[na + i]) {
                    return false;
                }
                i = i + 1;
            }
        }
        // Both consumed this level; are there more?
        let f_more = fend < len(filter);
        let n_more = nend < len(name);
        if !f_more && !n_more {
            return true;
        }
        if !f_more {
            return false;
        }
        if !n_more {
            // The filter has more levels: only a following `#` still matches
            // (`a/#` matches `a`).
            let rest = fend + 1;
            let r_end = level_end(filter, rest);
            return r_end - rest == 1 && int_of(filter[rest]) == 35 && r_end == len(filter);
        }
        fa = fend + 1;
        na = nend + 1;
    }
    return false;
}
