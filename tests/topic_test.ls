edition 5;

import std.test;
import mqtt.topic;

pub fn test_valid_names() -> [] int {
    test.assert(topic.valid_name("a", 100, 8));
    test.assert(topic.valid_name("a/b/c", 100, 8));
    test.assert(topic.valid_name("/", 100, 8));
    test.assert(topic.valid_name("a//b", 100, 8));
    test.assert(topic.valid_name("$SYS/x", 100, 8));
    // Wildcards are not allowed in a name.
    test.assert(!topic.valid_name("a/+", 100, 8));
    test.assert(!topic.valid_name("a/#", 100, 8));
    test.assert(!topic.valid_name("a+", 100, 8));
    test.assert(!topic.valid_name("", 100, 8));
    // Bounds.
    test.assert(topic.valid_name("a/b/c", 5, 3));
    test.assert(!topic.valid_name("a/b/c", 4, 3));
    test.assert(!topic.valid_name("a/b/c", 5, 2));
    return 0;
}

pub fn test_valid_filters() -> [] int {
    test.assert(topic.valid_filter("a", 100, 8));
    test.assert(topic.valid_filter("+", 100, 8));
    test.assert(topic.valid_filter("#", 100, 8));
    test.assert(topic.valid_filter("a/+/b", 100, 8));
    test.assert(topic.valid_filter("a/#", 100, 8));
    test.assert(topic.valid_filter("+/+", 100, 8));
    test.assert(topic.valid_filter("/", 100, 8));
    test.assert(topic.valid_filter("a//b", 100, 8));
    test.assert(!topic.valid_filter("", 100, 8));
    test.assert(!topic.valid_filter("a#", 100, 8));
    test.assert(!topic.valid_filter("#a", 100, 8));
    test.assert(!topic.valid_filter("a+", 100, 8));
    test.assert(!topic.valid_filter("+a", 100, 8));
    test.assert(!topic.valid_filter("a/#/b", 100, 8));
    test.assert(!topic.valid_filter("#/a", 100, 8));
    test.assert(!topic.valid_filter("a/b#", 100, 8));
    test.assert(!topic.valid_filter("a/++", 100, 8));
    test.assert(!topic.valid_filter("##", 100, 8));
    test.assert(!topic.valid_filter("a/#", 2, 8));
    test.assert(!topic.valid_filter("a/b/c", 100, 2));
    return 0;
}

pub fn test_matching() -> [] int {
    test.assert(topic.matches("a/b", "a/b"));
    test.assert(!topic.matches("a/b", "a/c"));
    test.assert(!topic.matches("a/b", "a"));
    test.assert(!topic.matches("a", "a/b"));
    test.assert(topic.matches("a/+", "a/b"));
    test.assert(topic.matches("a/+", "a/"));
    test.assert(!topic.matches("a/+", "a"));
    test.assert(!topic.matches("a/+", "a/b/c"));
    test.assert(topic.matches("+/+", "a/b"));
    test.assert(topic.matches("+", "a"));
    test.assert(!topic.matches("+", "a/b"));
    test.assert(topic.matches("#", "a"));
    test.assert(topic.matches("#", "a/b/c"));
    test.assert(topic.matches("a/#", "a"));
    test.assert(topic.matches("a/#", "a/b"));
    test.assert(topic.matches("a/#", "a/b/c"));
    test.assert(!topic.matches("a/#", "b"));
    test.assert(topic.matches("a/+/#", "a/b"));
    test.assert(topic.matches("a/+/#", "a/b/c/d"));
    test.assert(!topic.matches("a/+/#", "a"));
    test.assert(topic.matches("a//b", "a//b"));
    test.assert(!topic.matches("a//b", "a/b"));
    test.assert(topic.matches("+/b", "/b"));
    // A name starting with `$` is not matched by a leading wildcard.
    test.assert(!topic.matches("#", "$SYS/x"));
    test.assert(!topic.matches("+/x", "$SYS/x"));
    test.assert(topic.matches("$SYS/#", "$SYS/x"));
    test.assert(topic.matches("$SYS/+", "$SYS/x"));
    test.assert(topic.matches("a/#", "a/$b"));
    test.assert(topic.matches("a/+", "a/$b"));
    return 0;
}

pub fn test_levels() -> [] int {
    test.assert_eq(topic.levels("a"), 1);
    test.assert_eq(topic.levels("a/b"), 2);
    test.assert_eq(topic.levels("/"), 2);
    test.assert_eq(topic.levels("a//"), 3);
    return 0;
}
