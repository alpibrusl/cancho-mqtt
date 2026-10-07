# cancho-mqtt: design

> **Status: accepted by the maintainer (section 13); numbers still to be measured where marked.** Written before any code (issue #1). Every number below is a
> *choice*, not a measurement; each is marked **[fix]** (decided here, a test pins it) or **[measure]** (a claim that
> a later task must measure and may have to correct in place). Nothing in this document has been run.

An MQTT 3.1.1 broker in cancho. One thread, one `Poller`, memory sized at start, every input bounded, an authority
report that can be checked. The model is `cancho-cache`; the pattern, not the code, is reused.

## 1. Scope

**In (v1).** MQTT 3.1.1 over plain TCP, and MQTT 3.1 clients (`MQIsdp`, level 3; a CONNECT difference only, see §5).

| Packet | Direction | Codec |
|---|---|---|
| CONNECT | in | decode |
| CONNACK | out | encode |
| PUBLISH (QoS 0, 1, 2; retain; dup) | in and out | decode and encode |
| PUBACK | in and out | decode and encode |
| PUBREC, PUBREL, PUBCOMP | in and out | decode and encode (§7a) |
| SUBSCRIBE | in | decode |
| SUBACK | out | encode |
| UNSUBSCRIBE | in | decode |
| UNSUBACK | out | encode |
| PINGREQ | in | decode |
| PINGRESP | out | encode |
| DISCONNECT | in | decode |

That is the 14 control packets of the specification: the 14 are handled, and the four a client may never send (CONNACK, SUBACK, UNSUBACK, PINGRESP) are refused with a tag (`protocol.unexpected-packet`). *(Corrected by §7a: until QoS 2 was built, PUBREC, PUBREL and PUBCOMP were decoded only to be refused.)*

**Out.** Persistence, TLS, authentication, MQTT 5, websockets, `$SYS`, clustering. Issue #13 records why each
is out, in `docs/later.md`; the one-line reason for the authority row is in §2.

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
| `signals("INT,TERM")`, `signals_read` | graceful stop, so the log stream can end with its `end` record (edition 6) |
| `io_write`, `err_write` | the log stream, the introspect document, process-level errors |

**Corrected by measurement (issue #9):** an earlier draft wrote `net_in("<port>")`. The port is a command-line
argument, so the compiler cannot know it and derives `net_in("")`, as `examples/api` does. The row says the broker may
listen; which port is the perimeter's decision (`net.md` section 2). A broker built for one fixed port would carry
`net_in("1883")` and could not be tested on a free port, so v1 takes the unnamed row. Also, the ceiling lists
`conn_read`, `conn_write` and `err_write` before the code that uses them exists: rows are exact, so the derived set may
be smaller than the ceiling, never larger. The skeleton in `src/main.cho` derives `args`, `clock`, `conn_accept`,
`heap`, `io_write`, `net_in("")` and `poll`.

Never allowed, whatever `ceiling.toml` says (a list in `scripts/manifest.py`): `ffi` (TLS through OpenSSL, anything foreign;
makes the report unbounded; **corrected, `docs/later.md`:** cancho now has a pure TLS 1.3 client with no `ffi`, but it is a client
only, so a broker still has no TLS that avoids `ffi`), `net_out` (bridging), `io_read`, and every `fs`, `file` and `dir` label (persistence, logging to
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
memory growth under churn is a gate (G6). Every bound is a flag of `mqtt serve` (section 5a: one table drives the
parser, `introspect` and this document's check), with the default and hard ceiling in the table; a value outside
`[minimum, ceiling]` is refused at start with `args.out-of-range`. `tests/conformance/test_cli.py` parses this table
and fails if a default or ceiling here differs from what `mqtt introspect` reports.

| Bound | Flag | Default | Ceiling | Rule tag on overflow |
|---|---|---:|---:|---|
| Connections | `max-connections` | 1,024 | 16,384 | `limit.connections` |
| Packet size (whole packet incl. header) | `max-packet` | 16,384 | 262,144 | `limit.packet-size` |
| Client id length | `client-id-max` | 128 | 1,024 | `protocol.client-id-rejected` |
| Topic name or filter length | `topic-max` | 1,024 | 65,535 | `protocol.topic-invalid` |
| Topic levels | `topic-levels` | 16 | 64 | `protocol.topic-invalid` |
| Subscriptions per client | `subscriptions-per-client` | 32 | 1,024 | `limit.subscriptions-per-client` |
| Subscriptions total | `subscriptions-total` | 16,384 | 1,048,576 | `limit.subscriptions-total` |
| Trie nodes | `max-nodes` | 65,536 | 4,194,304 | `limit.subscriptions-total` |
| Outbound queue per session, bytes | `queue-bytes` | 32,768 | 1,048,576 | `limit.queue` |
| Outbound queue per session, messages | `queue-messages` | 64 | 1,024 | `limit.queue` |
| QoS 1 and 2 in-flight window per session (outbound) | `inflight` | 16 | 64 | (queues behind the window) |
| QoS 2 messages received and not yet released, per session | `qos2-inbound` | 16 | 1,024 | `limit.qos2-inbound` |
| Seconds between `$SYS` updates (0 for none) | `sys-interval` | 10 | 86,400 | (none) |
| Offline sessions kept (clean-session = 0) | `offline-sessions` | 256 | 16,384 | `limit.offline-sessions` |
| Retained messages | `retained-messages` | 1,024 | 65,536 | `limit.retained` |
| Bytes of one retained message, topic included | `retained-slot-bytes` | 1,024 | 262,144 | `limit.retained` |
| Bytes of a will, topic included | `will-bytes` | 1,024 | 65,536 | `limit.will-size` |
| CONNECT deadline, seconds | `connect-timeout` | 10 | 3,600 | `timeout.connect` |
| Write-stall deadline, seconds | `write-stall` | 30 | 86,400 | `timeout.write-stalled` |

Plus `port` (1883) and `stats-seconds` (10; 0 for none), which are not bounds. Two relations are checked at start:
`queue-bytes` must be at least `max-packet + 264` (a packet of the largest size plus its entry header and the 256
bytes kept free for control packets must fit a queue), refused as `args.conflict`.

**Corrected by building (issue #5).** An earlier draft of this section said defaults of 65,536 bytes for the packet and
64 KiB for a queue, an "output ring per connection", and that "message payloads are held once and shared by reference
count between every subscriber's queue". None of that is how it was built. A session's queue *is* its output (there
is no second ring); each subscriber's queue holds its own encoded copy of every message (a subscriber costs a copy, not
a reference), which is simple, keeps every queue's bytes bounded by its own bound, and makes a PUBLISH to a subscriber
one `memmove` away from the socket; the price is that fan-out of a large payload to many subscribers copies it that many
times. Sharing is a possible later optimisation, to be taken only if B1 and B2 say the copies matter. The defaults were
lowered to 16 KiB and 32 KiB so that the tables the broker allocates at start are about 73 MiB for the default bounds
(the `listening` record's `memory_bytes`, which is `mqtt.config.memory`, an estimate of the tables alone).

**What "sized at start" means, measured (issue #11).** The tables are allocated at start from the bounds and never grow;
the operating system commits their pages as they are used. An idle broker is therefore small (about 3 MiB resident
with the default bounds, against 73 MiB of tables) and a full one is no larger than `memory_bytes` plus the program
(an input buffer and a queue buffer are attached to a connection or session only while they hold something, so an idle connection costs about 0.3 KiB resident, see `docs/benchmark.md`) (`test_memory.py` asserts both, and that the resident size after 6,000 rounds of connects, sessions, subscriptions,
retained messages, wills and refusals is within 1 MiB of what it was after a warm-up). The claim is that memory has a
ceiling stated at start, not that all of it is touched at start.
That holds with 4 KiB pages. With transparent huge pages set to `always` the operating system makes a whole 2 MiB region resident on
the first touch of any byte in it, so resident size grows in 2 MiB steps (still never past `memory_bytes`); found on CI, where a
resident-size test grew by one huge page late in its run. The memory tests therefore run the broker with THP disabled for the process.

## 5. Rules: every refusal has a tag and a defined action

The tag appears in the broker's counters and in the test that names it. "Close" is a TCP close with no further bytes
unless stated. Spec references are to MQTT 3.1.1 (OASIS, 2014).

| Tag | Trigger | Action | Spec |
|---|---|---|---|
| `protocol.connect-first` | first packet is not CONNECT | close | 3.1.0-1 |
| `protocol.connect-twice` | second CONNECT | close | 3.1.0-2 |
| `timeout.connect` | no CONNECT within the deadline | close | |
| `protocol.bad-name` | name is neither `MQTT` nor `MQIsdp` | close | 3.1.2-1 |
| `protocol.unsupported-level` | name `MQTT` with a level other than 4, or `MQIsdp` with a level other than 3 | CONNACK 0x01, close | 3.1.2-2 |
| `protocol.client-id-rejected` | empty id with clean-session 0, or **any empty id from an `MQIsdp` client**; id over the bound; invalid UTF-8 | CONNACK 0x02, close | 3.1.3-8, 3.1.3-9 |
| `protocol.reserved-flags` | reserved bits set wrongly in any fixed header or CONNECT flags | close | 2.2.2-2, 3.1.2-3 |
| `protocol.remaining-length` | varint over 4 bytes, or not minimal where the spec requires | close | 2.2.3 |
| `limit.packet-size` | declared length over the bound; the broker closes *before* buffering | close | |
| `protocol.malformed-packet` | length disagrees with content, truncated field, trailing bytes | close | |
| `protocol.topic-invalid` | PUBLISH topic contains a wildcard, NUL, bad UTF-8, or exceeds a bound | close | 3.3.2-2, 4.7.3 |
| `protocol.filter-invalid` | SUBSCRIBE or UNSUBSCRIBE filter breaks the wildcard rules | SUBACK 0x80 for that filter; UNSUBACK ignores it | 4.7.1 |
| `protocol.packet-id` | packet id 0 where one is required | close | 2.3.1-1 |
| `protocol.qos3` | PUBLISH QoS bits are 3 | close | 3.3.1-4 |
| `limit.subscriptions-per-client`, `limit.subscriptions-total` | subscription bound reached | SUBACK 0x80 for that filter | |
| `limit.connections` | accept with the table full | close at accept, no CONNACK | |
| `timeout.keepalive` | no packet for 1.5 x keepalive (0 disables) | close; will is published | 3.1.2-24 |
| `timeout.write-stalled` | output ring full and no progress for the deadline | close; will is published | |
| `limit.queue` | a subscriber's queue is at a bound | the new message is dropped for that subscriber; counted | |
| `limit.retained` | a retained store is at a bound and the topic is new | the message is delivered live, not retained; counted | |
| `limit.offline-sessions` | offline-session table full when a new one would be kept | the oldest offline session is dropped | |
| `protocol.unexpected-packet` | a packet only a server sends (CONNACK, SUBACK, UNSUBACK, PINGRESP) | close | 2.2.1 |
| `limit.topic-level` | a SUBSCRIBE filter with a level over 64 bytes | SUBACK 0x80 for that filter | |
| `limit.will-size` | a will larger than `will-bytes` | CONNACK 0x03, close | |
| `limit.output-full` | no room in a session's queue even for a control packet | close | |
| `protocol.reserved-topic` | a client PUBLISH to a topic beginning `$SYS/` | acknowledged as usual (PUBACK or PUBREC), not routed or retained; counted | 4.7.2 |
| `limit.qos2-inbound` | a new QoS 2 PUBLISH while the session already holds `qos2-inbound` messages not yet released by PUBREL | close; will is published | |

**MQTT 3.1 (added after v1).** Name `MQIsdp` with level 3 is served as 3.1.1 is, with three differences, each compared with Mosquitto 2.0.18 by
`test_differential.py`: an empty identifier is refused (0x02) with either clean-session value; CONNACK never sets session-present (3.1 has no
such flag; the session is still resumed); and there is no 23-character identifier limit (3.1 lets a server allow longer; `client-id-max`
applies). A name and level that do not go together (`MQIsdp` 4, `MQTT` 3) are CONNACK 0x01. Nothing else in the protocol differs.

SUBSCRIBE at QoS 2 is **granted QoS 2** (§7a). A PUBREC, PUBREL or PUBCOMP for a packet identifier the broker does not know is not a refusal (§7a says what is answered).

**No input reaches a panic.** Every arithmetic and index in the codec is bounds-checked and the refusal above is what
the check produces. Fuzzing is G7.

## 5a. The agent-first surface: CLI, errors, logs

cancho programs are written for a reader that is a program first. The contract is cancho `docs/agent-toolbox.md`
(D2 to D7, D11), implemented once in the `contract/` package of `cancho-tools` (`toolbox.cli`, `toolbox.fail`,
`toolbox.rules`, `toolbox.describe`, `toolbox.out`). The broker **depends on that package, pinned by commit, and does
not copy it**: `[dependencies.*]` entries in `cancho.toml`, each a full `rev`, as `docs/package-system.md` section 8
prescribes. A broker is a long-running stream tool, not a one-shot document tool, so each rule below says where it
applies as written and where a server forces a deviation.

**Commands.** One binary, subcommands read by `toolbox.cli`:

| Command | Output | Exit |
|---|---|---|
| `mqtt serve [flags]` | NDJSON log stream (below), ends with an `end` record | 0 after a graceful stop |
| `mqtt introspect [--output json]` | one document: version, compiler pin, every flag with type, default and ceiling, the rule catalogue with exit codes, the exit-code table, the limits, the authority report | 0 |
| `mqtt skill` | the agentskills.io `SKILL.md`, generated from the same tables | 0 |
| `mqtt rules` | NDJSON, one `rule` record per connection-level rule with its tag and what the broker does, then an `end` record: the table `introspect` has no place for (see the gap below) | 0 |

`mqtt --authority` (issue #9) is replaced by `mqtt introspect`, which carries the same report in its `authority`
field. Unknown flags, flags given twice, a value on a boolean flag, a missing value: refused with `args.*`, never
ignored (`docs/flags.md` section 1).

**One table drives everything.** Every bound in section 4 is a row of the flag table (`name|short|kind|role|default|
help`, kind `nat`), with its ceiling in the limits table. The parser, `introspect` and `skill` read those two tables;
there is no second list. The numbers in section 4 are therefore typed once, in those tables, and this document's copy is
checked against them: a test fails when the table and section 4 disagree. Every flag has role `none`: no flag names a path,
a root or a write, so no repair that raises a bound can widen authority (D6 rule 1).

**Errors are data.** A refusal of the *process* (bad flags, cannot bind) is the D3 envelope on **stdout**, `{ok:false,
command, schema, error:{code, rule, message, hint, repair, detail}, errors:[...]}`, stderr empty except `internal.*`.
Independent argument errors are all reported together, in input order, and the first decides the exit status. Exit
codes are D4's: 0 success, 1 general (`io.*`, `internal.*`), 2 invalid arguments, 4 permission denied (a port below
1024), 5 conflict (`conflict.address-in-use`), 6 unused (the broker has a `Clock` but never a deadline of its own to
report; a supervisor kills), 8 and 9 unused. Exit 132 (a trap) is a bug and gate G7 says none is reachable.

**Rule tags** are `<area>.<name>` and are the section 5 names. Section 5's tags were written before this section and
are renamed to the catalogue form (`limit.packet-size`, `timeout.keepalive`, `protocol.malformed-packet`,
`unsupported.qos2-publish`, ...); the table above lists them. Process-level tags (`args.*`, `io.*`, `conflict.*`)
live in the tool's own `extra_rules` beside the shared catalogue. **Connection-level tags do not decide an exit
status**, since a refused client is not a failed process, so `toolbox.describe` has no row shape for them (its
catalogue is `tag|exit|repairable|summary`). That is a gap in the shared package, listed in section 12; until it is
closed they are published by the `mqtt rules` command (tag and action, as NDJSON), and a proposed change to
`toolbox.describe` goes to `cancho-tools` rather than a fork here. Every tag has a fixture
(`tests/conformance/test_rules.py`), and a test checks the fixtures are exactly the tags `mqtt rules` lists.

**Repairs** exist only where a script can apply one without judgement and never widen authority: `args.unknown-flag`
(nearest flag from the broker's own table), `args.out-of-range` (retry with the ceiling, never above it). A bound
already at the ceiling is `repair.kind:"none"` with the reason. `conflict.address-in-use` is `none`: choosing another
port is the caller's decision, not a script's.

**Logs are the stream.** `mqtt serve` writes one JSON object per line to stdout, in a deterministic key order, and
**ends with an `end` record** `{"type":"end","ok":...,"complete":...,counts}` on graceful stop (SIGINT or SIGTERM,
through cancho `Signals`, a handle the `Poller` waits on). A stream with no `end` record is truncated (D2): a
kill flushes nothing. Record types, all bounded:

| `type` | When | Bound |
|---|---|---|
| `listening` | once, after bind | 1 |
| `refusal` | a connection-level rule fired | the first occurrence per rule per second; the rest are counted into `suppressed` on that record, so a flood of bad packets cannot become a flood of log lines |
| `stats` | every `--stats-seconds` (default 10) | 1 per interval: connections, subscriptions, retained messages and bytes, queue drops, and a counter per rule tag |
| `error` | a process-level error after start | as `refusal` |
| `end` | on stop | 1 |

Rules that follow from the contract: **no message payload is ever logged**; client ids and topics are written as
`text` when valid UTF-8 and as `{"b64":...}` when not, truncated at 32 bytes with `truncated:true` (D2); integers
only; every write is checked and a failed or short write ends the stream with `io.write-failed` on stderr and exit 1
(`toolbox.out`). **Deviation from D7:** a record carries `t_ms`, monotonic milliseconds since start, because a
server's log without time is not a log, and the broker holds the `clock` label by design (section 2). It carries no
wall-clock time (`clock_unix_ms`), so output is reproducible up to those offsets, and the record order is deterministic.
**Measured, and not fixed (G9, Gap 8): a log reader that stops reading stalls the broker.** The log is written to standard output
with a blocking write, and cancho has no non-blocking write for it, so once the pipe is full (64 KiB on Linux) the one thread blocks
and no client is answered. Measured with the pipe shrunk to 4 KiB and `--stats-seconds 1`: a client's PINGREQ went unanswered after
7 s (`test_a_log_reader_that_stops_reading_stalls_the_broker`, which asserts the stall, so that a language that gains a non-blocking
write fails it and this paragraph gets corrected). How fast the log fills, measured: **about 86 bytes a second idle (the `stats`
record every 10 s), so 13 minutes to fill 64 KiB; about 1.8 KB a second under a flood of refusals, so 36 seconds**. The volume is
bounded (refusals are rate-limited to one event per rule per second, the counters stay exact) but a reader that stops is not tolerated.
So: run the broker with standard output going to a supervisor or a file, never to a pipe a program may stop reading; and
`--stats-seconds 0` lengthens the interval but does not remove the risk. *Corrected: this section said the stall would be decided from a
measurement (drop and count, or end the stream); neither is possible today, and gate G9 said "a stalled log reader does not stall
clients", which is false.*

## 6. Backpressure: drop the newest, then disconnect

A slow subscriber must never grow memory without bound and never stall another. Three choices were open:

1. **Drop oldest.** Better for telemetry. It means moving or overwriting the queue's head while a write of it may be
   half done, and reordering the retransmit state of QoS 1. Rejected for v1.
2. **Drop newest (chosen).** O(1), the ring is only ever appended to at the tail and consumed at the head, ordering per
   subscriber is preserved, and a half-written message is never touched. I believe Mosquitto drops new messages when its queue is full, but that is
   from memory and unchecked; G3 compares behaviour. Cost: the subscriber sees an old message instead of the latest one.
3. **Disconnect on full.** The clean at-least-once answer for QoS 1, but a burst would disconnect healthy clients.
   Kept only as the last resort: a connection that makes **no write progress for 30 s** is closed (`timeout.write-stalled`).

The guarantee this gives, stated precisely: **QoS 1 is at-least-once for every message that was accepted into a
subscriber's queue.** A message dropped by `limit.queue` is not delivered and is not retried; the drop is counted and
visible. A broker restart loses every queue, every offline session and every retained message (§7, §8). This is weaker
than "at-least-once" as a bare phrase, and the README and the conformance table say so in the same words.

## 7. Sessions

`clean-session = 1`: all state is dropped at disconnect. `clean-session = 0`: subscriptions and the outbound QoS 1
queue are kept **in memory only**, up to the offline-session bound; on reconnect with the same client id the session
resumes (session-present = 1) and queued and unacknowledged messages are sent, with DUP on redelivery. If the table is
full, the oldest offline session is evicted (`limit.offline-sessions`). Takeover: a CONNECT with an id already connected closes the old connection, and because that close is not a DISCONNECT the
old connection's will **is** published (3.1.2-8) -- unless the takeover continues a persistent session (the old
connection and the new one both asked for clean-session 0), where the reconnecting client is not gone and no will is
published. The will is not published after a DISCONNECT (3.14.4-3).

**Corrected by the differential run (issue #10).** This section said a takeover does not publish the old will, citing
3.1.4-2, which only says the old connection is disconnected. Mosquitto publishes it, 3.1.2-8 says a will is published when the
connection is closed without a DISCONNECT, and the run showed the disagreement; the broker and its test were changed.
A second run then showed that Mosquitto does not publish it when a persistent session is taken over by a persistent
session (a client that reconnects quickly would otherwise be announced as gone while it is back), so this broker does
not either; the four cases of (old, new) clean-session are in `test_protocol.py`.

Memory competes between offline queues and retained messages only through the shared total in §4; each has its own
bound so neither can starve the other.

## 7a. QoS 2

*Added after v1 (the first item of `docs/later.md`'s order). Written before the code; where building or the differential run
finds a claim false, it is corrected here in place.*

**Delivery in, method A.** 3.1.1 (4.3.3) allows two ways to handle an inbound QoS 2 PUBLISH: deliver it onward when it
arrives and remember the packet identifier until PUBREL (method A), or store the message and deliver it on PUBREL (method
B). This broker takes **method A**: on a PUBLISH at QoS 2 it routes the message at once (retain, then every subscriber at
the lower of the two QoS), records the packet identifier in the session's *received* set, and queues PUBREC. A second
PUBLISH with an identifier already in the set (the client resending, DUP or not) is **not routed again**; PUBREC is sent
again. PUBREL removes the identifier and is always answered with PUBCOMP, known or not (the client must be able to finish).
Why A: the set holds identifiers, not messages, so its memory is `qos2-inbound` x 8 bytes a session whatever the payload;
method B would hold up to `max-packet` bytes per pending message, which this broker's "every bound a flag" rule would
then have to multiply. Cost: the exactly-once promise holds from the publisher's side (a message is routed once per
identifier per session), but a subscriber may receive the message before the publisher has completed the handshake. **I
believe Mosquitto does method B** (from memory, unchecked); the differential run (G3) compares what clients observe after
the handshake is complete, and the difference in *when* is written down in `KNOWN_DIFFERENCES` and asserted.

**Bound.** The received set is per session and bounded by the flag `qos2-inbound` (default 16, ceiling 1,024): a table of
that many identifiers a session, in one slab sized at start like the others. A new identifier past the bound is refused
as `limit.qos2-inbound`: close, will published. The message is **not** routed (the identifier cannot be remembered, so
exactly-once could not be kept). The set survives a disconnect of a persistent session (4.4: a session keeps QoS 2 messages
received and not completely acknowledged) and is dropped with the session (clean-session 1, eviction, takeover by a clean
session).

**Delivery out.** A subscription may now be granted QoS 2 and a message goes out at the lower of its QoS and the granted.
The queue entry (§4, `tables.cho`) gets one more *kind* and one more state machine:

| kind | what | states |
|---|---|---|
| 0 | QoS 0 PUBLISH | pending, done |
| 1 | QoS 1 PUBLISH | pending, sent (awaiting PUBACK), done |
| 2 | **QoS 2 PUBLISH** | pending, sent (awaiting PUBREC), done (PUBREC arrived) |
| 3 | control packet (PUBACK, SUBACK, ...) | pending, done |
| 4 | **PUBREL** | pending, sent (awaiting PUBCOMP), done |

On PUBREC for a kind-2 entry in state *sent*, the entry is marked done and a kind-4 entry with the same identifier is queued
behind it (it is a control packet: it takes the 256 bytes the queue keeps for them, so it cannot fail for want of room
unless the queue has no room even for four bytes, which closes as `limit.output-full`). On PUBCOMP the kind-4 entry is done.
The window (`inflight`) counts identifiers awaiting a reply: a kind-1 or kind-2 entry when sent, a kind-4 entry when sent;
PUBACK, PUBREC and PUBCOMP each release one. A kind-4 entry is never held back by the window (it replaces the slot a
PUBREC just released). A packet identifier is in use from the moment its PUBLISH is queued until PUBACK (QoS 1) or PUBCOMP
(QoS 2), including while it is only a PUBREL entry.

**Reconnect (persistent session).** What was sent and not acknowledged goes out again: a kind-1 or kind-2 entry still
awaiting its reply is resent as PUBLISH with DUP; a kind-4 entry is resent as PUBREL (which has no DUP flag). A kind-2
entry already past PUBREC is not resent as PUBLISH (3.1.1 4.3.3: the sender MUST NOT re-send the PUBLISH once PUBREC was
received). QoS 0 entries and control packets are dropped, as before.

**Answers to packets for identifiers the broker does not know.** PUBREC for an unknown identifier: ignored (counted, no
PUBREL: **a choice**, see what Mosquitto does in the differential run). PUBCOMP for an unknown identifier: ignored.
PUBREL for an unknown identifier: PUBCOMP. A PUBREC, PUBREL or PUBCOMP with identifier 0 is `protocol.packet-id`; with
wrong length `protocol.malformed-packet`; PUBREC and PUBCOMP with flags other than 0, or PUBREL with flags other than 2,
`protocol.reserved-flags` (2.2.2, 3.6.1, 3.5.1, 3.7.1).

**What changes elsewhere.** The two rules `unsupported.qos2-publish` and `unsupported.qos2-packet` are removed (the catalogue
is 27 rules, with `limit.qos2-inbound` added); `introspect`, the schema and `mqtt rules` follow from the tables. The flag
table gains `qos2-inbound`. `docs/conformance.md` gets the statements QoS 2 adds. The differences with Mosquitto that were
written down for SUBSCRIBE at QoS 2 (we granted 1) go away; overlapping filters (highest QoS here, first match there) remain.

**Memory.** The received set is `nsess x qos2-inbound x 8` bytes (about 160 KiB at the defaults and 1,280 sessions), touched
only for sessions that use QoS 2 (its slots are 128 bytes, so 32 sessions to a page). Nothing else grows: the queue kinds are
a byte in the entry header that already existed.

**Not done.** The cost of method A against method B is not measured (the broker has only A). QoS 2 fan-out throughput is in
`docs/benchmark.md` (added with this section); it was not run for v1, whose cells were QoS 0 and 1.

## 7b. `$SYS`

*Added after v1 (the first item of `docs/later.md`'s order). Written before the code, from what Mosquitto 2.0.18 was seen to do (probed with `mosquitto_sub`, not from its documentation), because the point of the feature is that tooling written for Mosquitto reads it.*

**Topics.** Seventeen, with Mosquitto's names and formats (decimal integers as text; `uptime` is `N seconds`; `version` is `cancho-mqtt version X`):
`$SYS/broker/` + `version`, `uptime`, `clients/total`, `clients/connected`, `clients/disconnected`, `clients/maximum`, `messages/received`,
`messages/sent`, `publish/messages/received`, `publish/messages/sent`, `publish/messages/dropped`, `publish/bytes/received`,
`publish/bytes/sent`, `bytes/received`, `bytes/sent`, `subscriptions/count`, `retained messages/count` (the space is Mosquitto's).
**Not provided, and said so:** `load/*` (rolling one, five and fifteen minute averages), `heap/*` (the broker does not track a heap
figure), `store/*` and `messages/stored`, `clients/active`, `clients/inactive`, `clients/expired` (deprecated in Mosquitto),
`shared_subscriptions/count` (there are no shared subscriptions). A subscriber to `$SYS/#` sees only what is in the list.

**Meaning, where this broker's answer differs in kind from Mosquitto's.** `clients/connected` is connections with a session;
`clients/disconnected` is persistent sessions with no connection; `clients/total` is their sum; `clients/maximum` is the most `total` has
been. `publish/messages/sent` and `publish/bytes/sent` count PUBLISHes **queued** for a subscriber (payload bytes), not written to the
socket, so a message dropped by `limit.queue` is counted as dropped and not as sent, and a message still in a queue is already sent. The
`bytes/*` and `messages/*` counters are socket bytes and MQTT packets of every type. `retained messages/count` counts the retained
messages clients published, **not** the `$SYS` topics themselves (Mosquitto counts its own, so the numbers differ by a constant there).

**Delivery.** Not through the retained store: the `$SYS` values live in a table of their own and cost none of the `retained-messages`
bound. A subscription whose filter matches a `$SYS` topic is sent its current value at once, with the retain flag set, as a retained
message would be; every `sys-interval` seconds (flag, default 10, 0 for none) each topic is sent to the **connected** sessions that
match, with the retain flag clear. Both are QoS 1 messages, delivered at the lower of 1 and the granted QoS (Mosquitto does the same).
One seen difference in the first delivery: Mosquitto sends `clients/maximum` first as a live update (retain flag clear) rather than as
a retained message, because it publishes that topic only once it has changed; here it is delivered like the other sixteen.
Offline persistent sessions are not sent the periodic updates (Mosquitto queues them; here a monitoring value that is stale on arrival
would only fill the queue). A filter that begins with a wildcard does not match a `$` topic (4.7.2-1, already true of the matcher).

**Publishing to `$SYS/...` from a client.** Mosquitto drops it without telling the client. So does this broker, with a tag now
(`protocol.reserved-topic`: acknowledged as usual, not routed, not retained, counted), so that a client cannot forge a statistic and the
refusal is visible. Any other `$` topic is an ordinary topic, as in Mosquitto.

**Cost.** Seven counters (bytes and packets each way, publish count and payload bytes in, payload bytes out, client high-water) in the
counter array that already exists, an increment each; a tick of seventeen small formatted values and seventeen matches per
`sys-interval`; a table of static names. Nothing grows with the number of connections. **Not measured:** the per-increment cost on the
fan-out path; `docs/benchmark.md` is re-run after this if the cost is visible.

## 8. Retained messages

A retained PUBLISH replaces the stored message for its topic; an empty retained payload clears it (3.3.1-6, 3.3.1-10).
Stored in a fixed table of `retained messages` and `retained bytes` bounds. **When full, refuse (chosen), do not
evict:** a new retained topic is not stored (`limit.retained`) while updates and clears of existing topics always
succeed. Reasoning: eviction would silently discard a retained message some other client relies on, and the choice of
victim (oldest, least used) is a policy with no right answer in v1; refusing is deterministic and visible. On
SUBSCRIBE, matching retained messages are delivered with RETAIN = 1; a live PUBLISH forwarded to an existing
subscriber has RETAIN = 0 (3.3.1-9).

## 9. Topics and the subscription trie

Validation per 4.7. A trie by level in fixed tables (node count bounded by `limit.subscriptions-total` x `levels`, a figure printed
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
| G4 | authority (#9) | `cancho authority` labels are a subset of the ceiling; no foreign symbol; bounded | a mutant adding `file_write` |
| G5 | fan-out (#6) | stalled subscriber leaves RSS flat; ordering per subscriber; no delivery to non-matching; 1,000 subscribers; peak RSS reported | mutant that removes the queue bound |
| G6 | memory (#8, #11) | RSS after a 10-minute churn run is within the printed budget plus a fixed overhead recorded in the first run | mutant that leaks a session on takeover |
| G7 | hardening (#11) | a fixed seed set (recorded in the repo) of byte streams split at random points, malformed and oversized lengths, slow and resetting clients, causes no trap; every bound tested at its edge | mutant that removes a bounds check |
| G8 | style | `cancho fmt --check`, no file over 2,000 lines, every rule in §5 has a test naming its tag | a 2,001-line file |
| G9 | agent surface (section 5a; issue #5) | `introspect` validates against its schema; its flag table equals section 4; every rule tag has a fixture; each process-level error is valid envelope JSON with the exit code the catalogue says; SIGTERM yields an `end` record with `complete:true`; **(corrected: a stalled log reader stalls the broker, and that is asserted as a known limitation, section 5a)** | a build whose flag default differs from section 4; a catalogue tag with no fixture |

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

**Corrected after running it (issue #12).** The cells above are the plan. What ran differs, and `docs/benchmark.md` ("Cells as
run") says how: fan-out used 4 publishers; B3 and B4 became a closed-loop probe with 0 and 1,000 idle connections (no
fixed-offered-load latency cell); B5 ran without retained messages; B6 measured the load generator, not the brokers. The
comparison covers five incumbents (Mosquitto, NanoMQ, EMQX, VerneMQ, HiveMQ CE), not Mosquitto alone, and the load generator
is `emqtt-bench` as planned. The honest outcome the plan expected ("a loss is plausible") was a mixed one: see the document.

## 12. What cancho gives, and gaps to confirm in #2

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
- **Gap 7: `toolbox.describe` has no shape for rules that are not exit statuses** (section 5a). To be proposed to `cancho-tools` (not yet raised); until accepted the broker publishes them with `mqtt rules`.
- **Gap 8: standard output blocks.** A log reader that stops reading stalls the poller: **measured** (section 5a): 7 s with a 4 KiB pipe;
  13 minutes to fill 64 KiB idle, 36 s under a flood. cancho has no non-blocking write for `Io`; to be raised there (a write that
  answers `Again`, or an `Io` the poller can watch). Until then the operator must keep reading.
- **Gap 9: `toolbox.describe` prints the toolbox's evidence list, not this program's.** `mqtt introspect` says its gates
  are "M1 schema conformance ... M9 memory flatness", "each a test in cancho-tools/tests/conformance". For this broker that
  is false: its gates are G1 to G9 of section 10, in `tests/` and `scripts/` here. To be proposed upstream as a field of
  `describe.Tool` (not yet raised). `mqtt skill` likewise says "`--format text`" and a read-only guarantee in generic words.
- **Gap 10: saturated QoS 0 fan-out is bimodal run to run** (about 350k or 650k deliveries/s on one core, `docs/benchmark.md`).
  **Cause found and fixed** (placement of the load generator against one write per delivery; fixed by write coalescing,
  `docs/benchmark.md`); the cell is now generator-limited.

None of these has been verified for this program. The first task of #2 is to build a loop that accepts 1,000 sockets
and report which of them bite.

## 13. Decisions taken

The maintainer accepted the recommendations in this document (2026-10-06): drop the newest message for a slow
subscriber and disconnect after 30 s without write progress (section 6); refuse new retained topics when the store is
full (section 8); one copy at the highest QoS for overlapping filters, to be compared with Mosquitto by the
differential harness (section 9); `emqtt-bench` as the load generator, revisited in #12 if it cannot be installed in
CI (section 11).
