# What is not in v1, and what taking it up would take

> **Status: decision records for issue #13. No code.** Each section says why the feature is out of v1, what it would cost,
> what would have to be true to take it up, and what it does to the authority row (`docs/design.md` §2). Claims about this
> broker are from reading its sources or from `docs/benchmark.md`; claims about lex-sys are from the files named, read for
> this document at the pin `lex-sys.toml` names; a claim I could not check is marked **not checked**. **Costs are estimates,
> not measurements, unless a number is attached to a measurement.** None of these is scheduled; the maintainer decides order.

## Summary

| Feature | Out of v1 because | Cost, in one line | Authority row change |
|---|---|---|---|
| QoS 2 | v1 had to be diffable against Mosquitto at QoS 0 and 1 first | a per-session table and four more packet flows; small memory | none |
| Persistence | it adds file effects and a disk that can stall the one thread | an append log, fsync policy, bounded recovery | `fs` label(s), forbidden today |
| TLS | the OpenSSL path makes the report unbounded; the pure client is client-only | server handshake, certificate and key files, about 179 KiB a connection by the client's layout | `ffi` (OpenSSL) or `fs` read (pure) |
| Authentication | needs a credential source and a hash | a credential file, a password hash, CONNACK 4/5, rule tags | `fs` read of one path, or flags |
| MQTT 5 | it changes the codec, every rule and the memory model | the largest item here: a second protocol | none |
| WebSockets | HTTP upgrade and framing are a second front end | upgrade, SHA-1 and base64, frame codec; about 10 KB a connection measured elsewhere | none |
| `$SYS` | nothing in v1 needed it; the `stats` log record serves agents | a topic subtree fed from counters that exist | none |
| Clustering | one thread, one `Poller`, no outbound connections | peers, subscription propagation, failure handling | `net_out`, forbidden today |

The order I would take them in, if asked, is in the last section. It is a recommendation, not a decision.

## QoS 2

**Why out.** v1 set out to be checkable against Mosquitto before it grew. QoS 0 and 1 are exercised end to end
(`test_differential.py`); QoS 2 doubles the protocol state to get right. A QoS 2 PUBLISH is refused with
`unsupported.qos2-publish`, and PUBREC, PUBREL and PUBCOMP are decoded and refused with `unsupported.qos2-packet` (§1, §5),
so a client that asks for it learns at once, by a tag, not by silence.

**Cost.** The codec already has the packet shapes. New state: inbound, a set of packet ids received and not yet released
(the broker must not deliver a duplicate while PUBREL is pending, 4.3.3); outbound, the queue entry's `state` byte (it holds
0 to 2 today, `tables.ls`) grows two states and the in-flight window counts them. Both are per session and bounded by the
existing `inflight` flag, so no new global table. Memory: **not measured**; by the layout it is a small per-session addition,
not a per-message one, so it should not move the idle cost (`docs/benchmark.md`, "Memory").

**What would have to be true.** A user who needs exactly-once, or a client library that refuses QoS 1 (**not checked**:
whether any in use does). The differential harness would first be extended with QoS 2 scenarios; the two known differences
(§9: overlapping filters, SUBSCRIBE at QoS 2 granted as 1) change, since SUBSCRIBE at QoS 2 would then be granted as 2.

**Authority.** None. Rows are the same.

## Persistence

**Why out.** The ceiling forbids every `fs`, `file` and `dir` label (`scripts/manifest.py`), and that is a property worth
keeping until something needs more. v1 keeps sessions, queues and retained messages in memory only (§7, §8): a restart loses
them, and a client that cares reconnects and resubscribes. That is a stated limit, not a bug.

**Cost.** What lex-sys has: handles that append, sync and rename, built and tested (`docs/file-writes.md`); `Fs(prefix)`
narrows to a path prefix (`docs/filesystem.md`). The broker would need: an append-only log of retained messages and persistent
sessions' subscriptions and queued messages, a compaction by write-then-rename, a recovery that rebuilds the tables within
the same bounds (a log larger than the bounds must be refused with a tag, not truncated silently), and a policy for when to
sync. The hard part is the one thread: a write or sync that waits stops the `Poller`, which is gap 8 (standard output blocks)
again, now on the hot path. Options: sync on a timer and say how much a crash can lose, or sync per record and take the
throughput hit; **neither is measured**. A QoS 1 acknowledgement that promises durability needs the second.

**What would have to be true.** A deployment that cannot tolerate losing retained messages or offline queues on restart, and
a stated loss budget. A log package to build on: `docs/file-writes.md` names `lexsys-log` as the asker; **not checked**
whether it exists as a package or is usable here.

**Authority.** New rows, exact: an `fs` label narrowed to the data directory (`Fs(prefix)` makes the prefix part of the row),
and none other. The ceiling's never-allowed list changes in the same commit, with a mutant that proves a read outside the
directory is refused. This is the smallest authority growth in this document and a real one: the report stops saying "no
filesystem".

## TLS

**Why out.** Two reasons, one of which is now out of date, so it is corrected here and in §2.
- *Through OpenSSL* (`docs/tls-nonblocking.md`, built for a client in `lexsys-hooks`): the report becomes `UNBOUNDED` ("a library
  is not an authority domain"), with 37 foreign symbols listed for a client, so the checkable ceiling this broker is built around
  is gone. §2 says this and it is still true of that path.
- *Pure lex-sys* (`packages/tls`, `docs/tls-pure.md`, `tls-core.md`, `tls-parity.md`): a TLS 1.3 **client**, with no `ffi`. It is
  a client only; a broker needs the **server** side (ClientHello parsing, the server's key share and certificate messages, a
  signature with the server's private key, session tickets if wanted). `docs/tls-pure.md` is explicit that nothing built on it
  may be called production-ready before its independent review (#209); **whether that review happened is not checked.**

**Cost.** A server handshake that does not exist; a certificate chain and a private key read from files at start; a
bounded handshake state per connection. For scale: the client's slot is **181,927 bytes, about 179 KiB per connection**
(`docs/tls-core.md`), against 0.34 KiB for an idle plain connection here today. At 5,000 connections that layout would hold
about 0.9 GB. The lazy-attach scheme from the memory fix applies directly (a slot only while a handshake or a record is in
flight is not enough: an established session needs its keys, so the floor is the per-session keys and counters, much smaller
than the slot, **not measured**). CPU: the pure client's handshake is about 2.8 ms, 4 to 7 times OpenSSL's 0.6 ms
(`docs/tls-hooks.md`, `tls-nonblocking.md`; both client-side), so a server doing a handshake per connect on one thread is the
connect-rate ceiling: about 350 a second on the pure path by that figure, about 1,500 through OpenSSL, **both client figures
used as stand-ins**. Resumption exists for the client (`docs/tls-resumption.md`).

**What would have to be true.** A deployment that cannot put TLS in front of the broker (a terminating proxy such as a load
balancer or stunnel does it today with no change here, and is the answer I would give first), and a server-side TLS in
lex-sys that has passed its review. If the answer is OpenSSL, the maintainer must accept an unbounded report for this broker,
which the project's premise argues against.

**Authority.** OpenSSL: `ffi(...)` and an `UNBOUNDED` report, never allowed today. Pure: no `ffi`; an `fs` read label narrowed to
the certificate and key paths (the key is a secret, and the report would then say so), plus `clock` and a randomness source
(**not checked**: how the pure client obtains randomness and which label it adds).

## Authentication and authorisation

**Why out.** v1 is anonymous: the codec decodes the user name and password fields (`wire.ls`, `c_user`, `c_pass`) so a client
that sends them is not refused, and the broker ignores them. There is no credential source, and a broker without one cannot
check anything.

**Cost.** (1) A credential source. A file read at start (user name, salted hash) is the only shape that keeps secrets out of
the command line; passing them as flags would put them in `ps` and in the `introspect` document, which this project's rules
forbid. (2) A hash. `std` has SHA-256 and SHA-512 and, per `docs/hkdf.md`, HMAC and HKDF; a password hash that resists guessing
(PBKDF2 over HMAC, or similar) is **not checked** to exist and is not something to write casually. (3) CONNACK return codes 4
(bad user name or password) and 5 (not authorised), each with a rule tag and a fixture. (4) Authorisation, if wanted: a check
per SUBSCRIBE filter and per PUBLISH topic, which puts a lookup on the publish path; its cost against the 1.2M/s fan-out is not
measured. (5) A rate limit on failed attempts, or the broker is a password-guessing oracle.

**What would have to be true.** A deployment that is not behind a network perimeter, and a decision on what a credential file
is (format, who writes it, how it is reloaded: a reload means a signal or a restart, since the poller does not watch files).

**Authority.** An `fs` read label narrowed to the credential file, nothing wider. A broker that reads a password file can read
only that path, and the report says so.

## MQTT 5

**Why out.** It is the largest item here and the one that would most change the code. MQTT 5 adds properties on every packet,
reason codes and reason strings on acknowledgements, session expiry in place of clean-session, topic aliases, shared
subscriptions, subscription options (no-local, retain-as-published, retain handling), flow control (receive maximum), server
disconnect with a reason, and enhanced authentication. The codec, the 28 connection rules (each maps to one or more 5 reason
codes), the queue entry format, the session model and the memory bounds all change.

**Cost.** A second codec with the first kept, since 3.1.1 clients do not go away; per-connection protocol level; properties
bounded and stored per message where they are forwarded (**memory per queued message grows**, so the `queue-bytes` default and
the queue's 8-byte entry header are revisited); a differential harness against Mosquitto at level 5; new rule tags for the
property-level refusals. **Not estimated in lines**; this is a project, not a feature.

**What would have to be true.** A client population that needs 5's features (shared subscriptions and session expiry are the
usual reasons), and a broker that is otherwise settled. Doing it before QoS 2 and persistence are done would build 5's session
model twice.

**Authority.** None.

## WebSockets

**Why out.** A browser or a constrained network that cannot open raw TCP needs MQTT over WebSocket. It is a second front end
to the same broker, not a change to the broker.

**Cost.** An HTTP/1.1 upgrade on the listener (`packages/http-server`, `http-request` exist, **not checked** whether they fit a
`Poller` loop with 5,000 sockets); the accept key needs SHA-1 and base64, which `std` lacks: `examples/ocpp_ws/{sha1,b64}.ls`
carry differentially tested versions (`docs/websocket-spike.md`, gap 1, which proposes them for `std`); a frame codec with
masking, fragmentation, ping and close, and a rule tag for each malformed frame; MQTT packets carried one or more per frame.
Memory: that spike's server held 10,000 WebSocket connections in 79.8 MB, 38 MB of it slabs allocated before the first
connection, so about 4 KB a connection above the base; **a different program, so a guide and not a prediction.** The input
buffer scheme added in #16 would help, since a connection holds a buffer only while a frame is split.

**What would have to be true.** A user with browser clients, and SHA-1 and base64 in `std` or carried here with the same
differential test.

**Authority.** None new (the same `net_in` listener, or a second `net_in` label if a second port is wanted: the row names the
kind, not the port, today).

## `$SYS`

**Why out.** Mosquitto publishes broker statistics under `$SYS/#`. v1's counters already exist (`tables.ls`, `ctr`, with an
exact count per rule) and the `stats` log record carries them on a schedule, which is the surface this broker's users (agents,
by the agent-first rule) read. A second surface for the same numbers had no asker.

**Cost.** Small. A retained-like subtree fed from the counters on the stats interval; the matcher already keeps a leading wildcard from matching a `$` name (`topic.ls`, `subs.ls`; spec 4.7.2-1, number from memory), so a client subscribing to `#` does not receive them. The cost is the fan-out of a periodic publish to every
`$SYS/#` subscriber, bounded by the same queue rules.

**What would have to be true.** A user whose tooling expects `$SYS` (Mosquitto dashboards, several exporters do; **not checked**
which).

**Authority.** None. The counters are already in memory.

## Clustering

**Why out.** It conflicts with the design, not merely with the schedule. One thread and one `Poller` are the execution model;
`net_out` is never allowed, because a broker that can dial out can be pointed at anything; and nothing in v1 persists the state
a cluster would have to agree on.

**Cost.** Peer discovery and connection (outbound connections), propagation of subscriptions so a publish on one node reaches a
subscriber on another, retained-message and session ownership across nodes, and partition behaviour (what a client sees when
the cluster splits). Each is a distributed-systems decision, and the benchmark's comparison set (EMQX, VerneMQ, HiveMQ) are
clustered brokers with years in these decisions. **No estimate.**

**What would have to be true.** A load one node cannot carry (the benchmark suggests one core carries far more than the
Mosquitto it was compared with, so that is a high bar), or an availability requirement a single node cannot meet.

**Alternatives that need no change here.** A bridge: a small separate client program that subscribes on one broker and
publishes to another. It holds the `net_out` authority in its own report, and this broker's stays as it is. Or a standby
started by a supervisor after a crash, accepting the loss persistence would remove.

**Authority.** `net_out("")`, never allowed today. A cluster protocol port would also add a second `net_in`.

## An order, if asked

This is my recommendation, and the maintainer's to change.

1. **`$SYS`** and **QoS 2**: small, no authority change, and both close gaps against the comparison brokers.
2. **Authentication**: the first feature that needs a file label, so it is where the "ceiling grows by one exact row" path
   gets exercised on something small, with its mutant.
3. **Persistence**: the same path, bigger, with the stall question measured first.
4. **WebSockets**, then **TLS** (terminated in front of the broker until the server side exists and is reviewed).
5. **MQTT 5** after the session model has settled. **Clustering** only on a stated need.

## What this document does not settle

Whether QoS 2 and `$SYS` are wanted by anyone, the maintainer's tolerance for an `UNBOUNDED` report, and the loss budget for
persistence are questions for the maintainer; they are not mine to assume. None of the costs here is a measurement of this
broker, and the TLS figures are another program's client side.
