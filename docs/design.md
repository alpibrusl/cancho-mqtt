# lexsys-mqtt: design

> **Status: accepted by the maintainer (section 13); numbers still to be measured where marked.** Written before any code (issue #1). Every number below is a
> *choice*, not a measurement; each is marked **[fix]** (decided here, a test pins it) or **[measure]** (a claim that
> a later task must measure and may have to correct in place). Nothing in this document has been run.

An MQTT 3.1.1 broker in lex-sys. One thread, one `Poller`, memory sized at start, every input bounded, an authority
report that can be checked. The model is `lexsys-cache`; the pattern, not the code, is reused.

## 1. Scope

**In (v1).** MQTT 3.1.1 over plain TCP.

| Packet | Direction | Codec |
|---|---|---|
| CONNECT | in | decode |
| CONNACK | out | encode |
| PUBLISH (QoS 0, 1; retain; dup) | in and out | decode and encode |
| PUBACK | in and out | decode and encode |
| SUBSCRIBE | in | decode |
| SUBACK | out | encode |
| UNSUBSCRIBE | in | decode |
| UNSUBACK | out | encode |
| PINGREQ | in | decode |
| PINGRESP | out | encode |
| DISCONNECT | in | decode |
| PUBREC, PUBREL, PUBCOMP | in | **decode, then refused** (`qos2-packet`, §5) |

That is the 14 control packets of the specification: 11 are handled, 3 are decoded only to be refused with a tag.

**Out.** QoS 2, persistence, TLS, authentication, MQTT 5, websockets, `$SYS`, clustering. Issue #13 records why each
is out; the one-line reason for the authority row is in §2.

## 2. The authority row

The program the compiler derives for `main` must be within this ceiling and no more. `scripts/manifest.py` derives
it, commits it as `manifests/mqtt.authority.json`, embeds it in the binary (`mqtt --authority` prints it), and fails
when a derived label, spelled with its argument, is not in `ceiling.toml` (gate G4, issue #9).

| Label | Why |
|---|---|
| `args` | the port and the bounds on the command line |
| `heap` | the tables, allocated once at start |
| `net_in("")` | `tcp_listen` on the configured port |
| `conn_accept`, `conn_read`, `conn_write` | the connections |
| `poll` | the `Poller` |
| `clock` | monotonic time for keepalive and timeouts (`clock_ms`) |
| `io_write`, `err_write` | the start-up line and fatal start-up errors |

**Corrected by measurement (issue #9):** an earlier draft wrote `net_in("<port>")`. The port is a command-line
argument, so the compiler cannot know it and derives `net_in("")`, as `examples/api` does. The row says the broker may
listen; which port is the perimeter's decision (`net.md` section 2). A broker built for one fixed port would carry
`net_in("1883")` and could not be tested on a free port, so v1 takes the unnamed row. Also, the ceiling lists
`conn_read`, `conn_write` and `err_write` before the code that uses them exists: rows are exact, so the derived set may
be smaller than the ceiling, never larger. The skeleton in `src/main.ls` derives `args`, `clock`, `conn_accept`,
`heap`, `io_write`, `net_in("")` and `poll`.

Never allowed, whatever `ceiling.toml` says (a list in `scripts/manifest.py`): `ffi` (TLS, anything foreign; makes the
report unbounded), `net_out` (bridging), `io_read`, and every `fs`, `file` and `dir` label (persistence, logging to
disk). `scripts/mutants.py` applies four mutations (a file read, a foreign call, a ceiling that lacks a label the
program uses, an embedded report that is not the compiler's) to a copy of the repository and requires the gate to
refuse each; all four are refused.

## 3. Execution model

One thread. `main` splits the world, releases `fs`, `ffi` and `io_read`'s source, opens one `Listener`, one `Poller`,
and loops:

```
loop:
    n = poller_wait(poller, events, timeout)   // timeout = time to the earliest deadline, capped
    for each (token, events): service that connection or accept
    run timers: keepalive, CONNECT timeout, write-stall
```

Level-triggered readiness (the `Poller`'s only mode). A connection is non-blocking from `accept`. Reads fill a bounded
per-connection input buffer; complete packets are decoded and handled in order; writes go to a bounded per-connection
output ring and, when the kernel does not take them all, the connection is watched for writability and **not read
from** until the ring drains below half (`docs/server.md`'s rule, `examples/api`). That is what stops a client that
never reads from making the broker buffer without bound.

Connections are held as tickets in `std.conns.Table` (a `Conn` is a resource and cannot live in a `Vec`;
`native-sockets.md` §10.3). Slot number is the poller token.

**Fairness.** A connection is serviced for at most one input buffer's worth of bytes per wake, so a flood from one
publisher cannot starve the rest. **[measure]** (benchmark cell B3).

## 4. Memory: sized at start, every bound has a rule

All state is allocated at start from the bounds below. After start the broker allocates nothing it did not account for;
memory growth under churn is a gate (G6). Bounds are command-line arguments with these defaults and the stated hard
ceilings (a larger value is refused at start with `bound-out-of-range`).

| Bound | Default **[fix]** | Hard ceiling | Rule tag on overflow |
|---|---:|---:|---|
| Connections | 1,024 | 16,384 | `connections-full` |
| Packet size (whole packet incl. header) | 65,536 B | 262,144 B | `packet-too-large` |
| Input buffer per connection | packet size | | (same) |
| Output ring per connection | 65,536 B | | `write-stalled` |
| Client id length | 128 B | | `client-id-rejected` |
| Topic name length | 1,024 B | 65,535 B | `topic-invalid` |
| Topic levels | 16 | | `topic-invalid` |
| Subscriptions per client | 32 | | `subs-per-client` |
| Subscriptions total | 16,384 | | `subs-total` |
| Per-subscriber outbound queue | 64 messages and 64 KiB | | `queue-full` |
| QoS 1 in-flight window per subscriber | 16 | 64 | (queues behind the window) |
| Offline sessions kept (clean-session = 0) | 256 | | `session-evicted` |
| Retained messages | 1,024 | | `retained-full` |
| Retained bytes | 1 MiB | | `retained-full` |
| CONNECT deadline | 10 s | | `connect-timeout` |
| Write-stall deadline | 30 s | | `write-stalled` |

The memory the broker needs is a function of these numbers: connections x (input + output) + queues + retained
store + the trie. `scripts/budget.py` (not yet written, issue #6) prints it for a given set of bounds, and the broker prints the same figure at
start. The claim "sized at start" is true when RSS after a churn run stays within that figure plus a fixed runtime
overhead. **[measure]** (G6, issue #11).

Message payloads are held once and shared by reference count between every subscriber's queue, so fan-out to 1,000
subscribers costs 1,000 queue entries, not 1,000 copies. Whether lex-sys's `Rc` or an index into a slab is the right
tool is a question for issue #6; the bound above holds either way. **[measure]** (peak memory at fan-out 1,000, G5).

## 5. Rules: every refusal has a tag and a defined action

The tag appears in the broker's counters and in the test that names it. "Close" is a TCP close with no further bytes
unless stated. Spec references are to MQTT 3.1.1 (OASIS, 2014).

| Tag | Trigger | Action | Spec |
|---|---|---|---|
| `connect-first` | first packet is not CONNECT | close | 3.1.0-1 |
| `connect-twice` | second CONNECT | close | 3.1.0-2 |
| `connect-timeout` | no CONNECT within the deadline | close | |
| `protocol-name` | name is not `MQTT` | close | 3.1.2-1 |
| `protocol-level` | level is not 4 | CONNACK 0x01, close | 3.1.2-2 |
| `client-id-rejected` | empty id with clean-session 0; id over the bound; invalid UTF-8 | CONNACK 0x02, close | 3.1.3-8, 3.1.3-9 |
| `reserved-flags` | reserved bits set wrongly in any fixed header or CONNECT flags | close | 2.2.2-2, 3.1.2-3 |
| `remaining-length-malformed` | varint over 4 bytes, or not minimal where the spec requires | close | 2.2.3 |
| `packet-too-large` | declared length over the bound; the broker closes *before* buffering | close | |
| `packet-malformed` | length disagrees with content, truncated field, trailing bytes | close | |
| `topic-invalid` | PUBLISH topic contains a wildcard, NUL, bad UTF-8, or exceeds a bound | close | 3.3.2-2, 4.7.3 |
| `filter-invalid` | SUBSCRIBE or UNSUBSCRIBE filter breaks the wildcard rules | SUBACK 0x80 for that filter; UNSUBACK ignores it | 4.7.1 |
| `packet-id-invalid` | packet id 0 where one is required | close | 2.3.1-1 |
| `qos2-publish` | PUBLISH with QoS 2 | close | |
| `qos2-packet` | PUBREC, PUBREL or PUBCOMP | close | |
| `qos3` | PUBLISH QoS bits are 3 | close | 3.3.1-4 |
| `subs-per-client`, `subs-total` | subscription bound reached | SUBACK 0x80 for that filter | |
| `connections-full` | accept with the table full | close at accept, no CONNACK | |
| `keepalive-expired` | no packet for 1.5 x keepalive (0 disables) | close; will is published | 3.1.2-24 |
| `write-stalled` | output ring full and no progress for the deadline | close; will is published | |
| `queue-full` | a subscriber's queue is at a bound | the new message is dropped for that subscriber; counted | |
| `retained-full` | a retained store is at a bound and the topic is new | the message is delivered live, not retained; counted | |
| `session-evicted` | offline-session table full when a new one would be kept | the oldest offline session is dropped | |

SUBSCRIBE at QoS 2 is **granted QoS 1** in SUBACK, which 3.1.1 allows (3.8.4). That is not a refusal; it has no tag.

**No input reaches a panic.** Every arithmetic and index in the codec is bounds-checked and the refusal above is what
the check produces. Fuzzing is G7.

## 6. Backpressure: drop the newest, then disconnect

A slow subscriber must never grow memory without bound and never stall another. Three choices were open:

1. **Drop oldest.** Better for telemetry. It means moving or overwriting the queue's head while a write of it may be
   half done, and reordering the retransmit state of QoS 1. Rejected for v1.
2. **Drop newest (chosen).** O(1), the ring is only ever appended to at the tail and consumed at the head, ordering per
   subscriber is preserved, and a half-written message is never touched. I believe Mosquitto drops new messages when its queue is full, but that is
   from memory and unchecked; G3 compares behaviour. Cost: the subscriber sees an old message instead of the latest one.
3. **Disconnect on full.** The clean at-least-once answer for QoS 1, but a burst would disconnect healthy clients.
   Kept only as the last resort: a connection that makes **no write progress for 30 s** is closed (`write-stalled`).

The guarantee this gives, stated precisely: **QoS 1 is at-least-once for every message that was accepted into a
subscriber's queue.** A message dropped by `queue-full` is not delivered and is not retried; the drop is counted and
visible. A broker restart loses every queue, every offline session and every retained message (§7, §8). This is weaker
than "at-least-once" as a bare phrase, and the README and the conformance table say so in the same words.

## 7. Sessions

`clean-session = 1`: all state is dropped at disconnect. `clean-session = 0`: subscriptions and the outbound QoS 1
queue are kept **in memory only**, up to the offline-session bound; on reconnect with the same client id the session
resumes (session-present = 1) and queued and unacknowledged messages are sent, with DUP on redelivery. If the table is
full, the oldest offline session is evicted (`session-evicted`). Takeover: a CONNECT with an id already connected
closes the old connection and the old one's will is **not** published (3.1.4-2). The will is published only on an
abrupt close (keepalive, reset, protocol error), not after DISCONNECT (3.14.4-3).

Memory competes between offline queues and retained messages only through the shared total in §4; each has its own
bound so neither can starve the other.

## 8. Retained messages

A retained PUBLISH replaces the stored message for its topic; an empty retained payload clears it (3.3.1-6, 3.3.1-10).
Stored in a fixed table of `retained messages` and `retained bytes` bounds. **When full, refuse (chosen), do not
evict:** a new retained topic is not stored (`retained-full`) while updates and clears of existing topics always
succeed. Reasoning: eviction would silently discard a retained message some other client relies on, and the choice of
victim (oldest, least used) is a policy with no right answer in v1; refusing is deterministic and visible. On
SUBSCRIBE, matching retained messages are delivered with RETAIN = 1; a live PUBLISH forwarded to an existing
subscriber has RETAIN = 0 (3.3.1-9).

## 9. Topics and the subscription trie

Validation per 4.7. A trie by level in fixed tables (node count bounded by `subs-total` x `levels`, a figure printed
at start). `$`-prefixed topics are not matched by a filter starting with `+` or `#` (4.7.2-1). A client with two
overlapping filters receives a message once per matching filter or once at the highest granted QoS: **the broker
sends one copy at the highest QoS**, which 3.1.1 permits (3.3.5). Whether Mosquitto does the same is unchecked; the differential harness (G3) compares
what clients observe and the document is corrected in place if they differ.

## 10. The gates

Each gate is fixed here, before the code it judges, and must be able to fail. A gate that cannot be shown to fail on a
mutant is not a gate.

| Gate | Judges | Passes when | Fails (demonstrated by) |
|---|---|---|---|
| G1 | codec (#3) | 1,000+ generated and fuzzed packets decode identically to a Python oracle; one byte at a time decodes the same as whole | mutant that skips the remaining-length bound |
| G2 | topics (#4) | trie matching equals a Python reference over generated filters and topics, including every wildcard edge, `$`, empty levels | mutant that lets `#` match mid-filter |
| G3 | conformance (#10) | every normative statement in the coverage table has a test naming it; differential scenarios against Mosquitto agree on what each client observes | removing a test fails the table check |
| G4 | authority (#9) | `lex-sys authority` labels are a subset of the ceiling; no foreign symbol; bounded | a mutant adding `file_write` |
| G5 | fan-out (#6) | stalled subscriber leaves RSS flat; ordering per subscriber; no delivery to non-matching; 1,000 subscribers; peak RSS reported | mutant that removes the queue bound |
| G6 | memory (#8, #11) | RSS after a 10-minute churn run is within the printed budget plus a fixed overhead recorded in the first run | mutant that leaks a session on takeover |
| G7 | hardening (#11) | a fixed seed set (recorded in the repo) of byte streams split at random points, malformed and oversized lengths, slow and resetting clients, causes no trap; every bound tested at its edge | mutant that removes a bounds check |
| G8 | style | `lex-sys fmt --check`, no file over 2,000 lines, every rule in §5 has a test naming its tag | a 2,001-line file |

Mutants of each new piece must all be killed or individually explained in the PR that adds the piece.

## 11. Benchmark, pre-registered

Fixed now; results are recorded in `docs/benchmark.md` honestly, including when worse. Same machine, quiet, loopback,
both brokers single-threaded, Mosquitto's version and config recorded, three runs per cell with the spread reported,
load generator `emqtt-bench` pinned by version (chosen over a script because it is the incumbent's usual partner;
revisited in #12 if it cannot be installed in CI).

| Cell | Measures |
|---|---|
| B1 | fan-out throughput QoS 0: 1 publisher, 100 subscribers, 64 B payload |
| B2 | fan-out throughput QoS 1: same |
| B3 | many connections: 1, 100, 1,000 idle plus one active pair, publish-to-receive latency p50/p99/p99.9 |
| B4 | publish latency percentiles at fixed offered load, 1 publisher, 1 subscriber |
| B5 | RSS at 1,000 connections; at 1,000 connections plus 1,000 retained messages |
| B6 | connect rate (connects per second, clean session) |

No claim of being faster is made before B1-B6 exist. The expected honest outcome in `native-sockets.md` §10.4 is that
the `Poller` is slower than `poll(2)` in the environment it was measured in, so a loss is plausible.

## 12. What lex-sys gives, and gaps to confirm in #2

From `docs/native-sockets.md`, `docs/listen.md`, `docs/tls-nonblocking.md`:

- **Gives:** `tcp_listen`, `tcp_accept`, `conn_read`, `conn_write` (never wait, `Again`), `conn_nonblocking`,
  `conn_nodelay`, `Poller` (token-based), `clock_ms`, `std.conns.Table`.
- **Gap 1: a `Conn` cannot be in a table**; tickets through `std.conns` cost two extra builtin calls per read or write
  (`native-sockets.md` §10.3). Cost is in B3, not argued.
- **Gap 2: `poller_wait` returns at most 64 events per call** (§10.1); with 1,000 busy connections that is many waits
  per round. Measured in #6.
- **Gap 3: the `Poller` is slower than `poll(2)` where it was measured** (§10.4). A decision for the compiler, not
  for this repository; the broker takes whichever the compiler ships.
- **Gap 4: no atomics**, so the broker is single-threaded by choice, as the cache is.
- **Gap 5: a region arena is capped at 64 KiB** in some paths (`docs/listen.md` §5). Whether this binds the 64 KiB
  packet bound is to be tested first in #3; if it does, the packet bound's default is lowered and this section says so.
- **Gap 6: `std.conns.Table` fields are readable** (`native-sockets.md` §6 correction); no effect here beyond noting
  that tickets are not authority.

None of these has been verified for this program. The first task of #2 is to build a loop that accepts 1,000 sockets
and report which of them bite.

## 13. Decisions taken

The maintainer accepted the recommendations in this document (2026-10-06): drop the newest message for a slow
subscriber and disconnect after 30 s without write progress (section 6); refuse new retained topics when the store is
full (section 8); one copy at the highest QoS for overlapping filters, to be compared with Mosquitto by the
differential harness (section 9); `emqtt-bench` as the load generator, revisited in #12 if it cannot be installed in
CI (section 11).
