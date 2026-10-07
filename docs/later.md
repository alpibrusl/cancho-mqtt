# What is not in v1, and what taking it up would take

> **Status: decision records for issue #13. No code.** Each section says why the feature is out of v1, what it would cost,
> what would have to be true to take it up, and what it does to the authority row (`docs/design.md` §2). Claims about this
> broker are from reading its sources or from `docs/benchmark.md`; claims about lex-sys are from the files named, read for
> this document at the pin `lex-sys.toml` names; a claim I could not check is marked **not checked**. **Costs are estimates,
> not measurements, unless a number is attached to a measurement.** None of these is scheduled; the maintainer decides order.

## Summary

| Feature | Out of v1 because | Cost, in one line | Authority row change |
|---|---|---|---|
| QoS 2 | v1 had to be diffable against Mosquitto at QoS 0 and 1 first | **built** (design §7a); about 270 lines of source, a per-session set of identifiers | none |
| Persistence | it adds file effects and a disk that can stall the one thread | an append log, fsync policy, bounded recovery | `fs` label(s), forbidden today |
| TLS | lex-sys has a TLS 1.3 *server* only as a design (`docs/tls-server.md`, nothing built); the OpenSSL path would make the report unbounded | wait for lex-sys steps 1 to 3; then flags, rules, about 179 KiB a connection by the client's layout, 3 to 5 ms of CPU a handshake | `fs` read of the certificate and key; no `ffi` |
| Authentication | needs a credential source and a hash | a credential file, a password hash, CONNACK 4/5, rule tags | `fs` read of one path, or flags |
| MQTT 5 | it changes the codec, every rule and the memory model | the largest item here: a second protocol | none |
| WebSockets | HTTP upgrade and framing are a second front end | upgrade, SHA-1 and base64, frame codec; about 10 KB a connection measured elsewhere | none |
| `$SYS` | nothing in v1 needed it; the `stats` log record serves agents | a topic subtree fed from counters that exist | none |
| Multi-threading | lex-sys has `spawn` and `join` but no atomics, no channel, no per-thread heap; one core was enough in every measurement | worker threads for TLS handshakes first; sharding the tables needs a channel and a way to wake a poller | new `conc` row |
| Clustering | one thread, one `Poller`, no outbound connections | peers, subscription propagation, failure handling | `net_out`, forbidden today |

The order I would take them in, if asked, is in the last section. It is a recommendation, not a decision.

## QoS 2

**Built** (the first of this document's order; design section 7a, `tests/conformance/test_qos2.py`). The estimate above said a
per-session table and four more packet flows and a small memory cost; that held. What it took: the codec's three encoders and
per-type flag checks, two queue kinds (QoS 2 message and PUBREL), a received-identifier set per session bounded by the new flag
`qos2-inbound` (default 16, 128-byte slots), the rule `limit.qos2-inbound` in place of the two `unsupported.qos2-*` rules, about
270 added lines of source, 20 black-box tests in `test_qos2.py` plus a rule fixture, two interop tests (paho and the Mosquitto clients) and
five differential scenarios (340 generated scenarios agree with Mosquitto, after a harness fix described in the PR). **Not what the estimate said:** the
inbound side is method A (routed on PUBLISH), where Mosquitto is method B (routed on PUBREL); the one difference is written down
and asserted (`KNOWN_DIFFERENCES`), and the SUBSCRIBE-at-QoS-2 difference is gone. **Not measured when this was written:** QoS 2
throughput; `docs/benchmark.md` has the cell if it has been run.

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

**The smallest cut: retained messages only.** Retained messages are kept in memory today (`retained-messages` slots, 1 KiB each by
default, so at most about 1 MiB at the defaults) and a restart loses them; sessions and queues are the bigger and harder part
(`clean-session 0` state, in-flight QoS 1 and 2 entries). A first step that persists only the retained set is: an append-only file
of set and clear records in one directory, written when a retained message changes (rare against QoS 0 traffic, so a sync per
record is a bounded cost, **not measured**), loaded at start within the same `retained-messages` bound (a file with more records
than the bound is refused with a tag, not truncated silently), compacted by writing a new file and renaming it. Authority: one
`fs` label narrowed to that directory. I ranked persistence after authentication (below) because authentication is what a broker
that is reachable beyond a trusted network needs first; that is a ranking by exposure, not by cost, and the maintainer should
swap them if a restart losing retained state is the bigger problem.

**What would have to be true.** A deployment that cannot tolerate losing retained messages or offline queues on restart, and
a stated loss budget. A log package to build on: `docs/file-writes.md` names `lexsys-log` as the asker; **not checked**
whether it exists as a package or is usable here.

**Authority.** New rows, exact: an `fs` label narrowed to the data directory (`Fs(prefix)` makes the prefix part of the row),
and none other. The ceiling's never-allowed list changes in the same commit, with a mutant that proves a read outside the
directory is refused. This is the smallest authority growth in this document and a real one: the report stops saying "no
filesystem".

## TLS

**Why out, corrected (2026-10-07).** v1 said TLS needs foreign code and would make the report unbounded. Two later facts change
that:
- lex-sys has a pure TLS 1.3 *client* (`packages/tls`, no `ffi`), and now a **design for the server side** that names this broker
  as one of its two askers (`docs/tls-server.md`, lex-sys #335: "status: design, its open questions answered as proposed
  (2026-10-07); nothing built yet"). Its first step, a constant-time `std.ecdsa.sign` on P-256, does not exist: `std.ecdsa` and
  `std.rsa` verify only and `std.ed25519.sign` branches on secret data, which that document names as the prerequisite.
- The engine never reads files: the program reads the certificate chain and the PEM key and hands the bytes over. So for this
  broker the authority row is an `fs` read narrowed to those two paths, **not `ffi`**, and the report stays bounded. The
  OpenSSL path (`docs/tls-nonblocking.md`: 37 foreign symbols for a client, an `UNBOUNDED` report) is no longer the only way.

So TLS is out of v1 because the server it would use is not built, not because it cannot be bounded. It is blocked on lex-sys
steps 1 to 3 (signing and key parsing, the server engine with interop and fuzz gates, an example to copy).

**What lex-sys's design says that matters here.**
- **TLS 1.3 only in its version 1.** Many embedded MQTT clients speak TLS 1.2 only (older mbedTLS and wolfSSL builds); its open
  question 1 left 1.2 for a later step "if any need it". Which of this broker's clients do is **not known**, and it decides whether
  TLS is usable for them.
- **Client certificates are step 4** and the design says they move ahead if a program makes them its default authentication.
  MQTT deployments commonly authenticate with them, so this broker's authentication design (below) should take a verified
  certificate subject as an identity from the start, instead of a password file only.
- **Cost.** A full handshake is about 3 to 5 ms of CPU, about 200 to 300 a second on a core, 10 to 50 times OpenSSL's (arithmetic
  from measured parts, per that document). On a single-thread broker that is the connect-rate ceiling, and a reconnect storm
  after a broker restart would queue behind it. Session tickets (step 5) save 2 to 3.5 ms of the 3 to 5.
- **Memory.** A server slot is the client's, about 179 KiB, so 5,000 TLS connections is about 0.9 GB by that layout, against 0.34
  KiB for an idle plain connection here after the buffers are attached lazily. The pool scheme of PR #16 helps with the
  handshake state only: an established connection needs its keys and record buffers, so its floor is smaller than the slot but
  **not measured**.
- **Review.** The client is "not independently reviewed" (#209) and a server that signs with a long-lived key is a larger
  exposure; the design says the broker's README must carry the same notice until that is answered.

**What this changes in the plan.** Nothing to build yet; two things to keep in view. (1) Our side can be designed before the
engine exists: flags (`--tls-port`, `--tls-cert`, `--tls-key`), rule tags for the engine's refusals, a `tls` field in the
`listening` record, and a handshake bound in the memory table; that is a design section, no code, when step 2 is close. (2) A
terminating proxy in front of the broker needs no change here and is still what I would recommend first.

**What would have to be true.** lex-sys steps 1 to 3 built and reviewed, a count of the clients that need TLS 1.2, and a
decision on client certificates as authentication.

**Authority.** An `fs` read label for the certificate and key paths (the key is a secret, and the report would say a file is
read), plus the `clock` and randomness the engine needs (**not checked**: which label the engine's DRBG seeding adds). No `ffi`.

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

## Multi-threading

**Why out.** Two reasons, one a fact about lex-sys and one a measurement.

*What lex-sys has* (read from `docs/threads.md`, `parallelism.md`, `atomics.md` and `thread-payloads.md` at lex-sys `2c1b315`):
`spawn` and `join` are real `pthread_create` and `pthread_join`, on both backends. What is missing is what a broker would share
across threads:
- **No atomics and no channel.** They are designed (`atomics.md`: one `Atomic` type, sequentially consistent operations, a channel
  as a library, edition 8, stages A0 to A3 each with its own gate) and not built; its stage A3 says the broker would be the
  second program to ask for the channel.
- **The checker does not make shared mutable data safe.** `atomics.md` §2 records that two threads writing one value was not
  stopped (fixed for `&!` by lending it until the join), and §9 says shared mutable structures, a hash map two threads insert
  into, are not solved: that needs a lock or a CAS-built library, "not scheduled". This broker's tables (sessions, trie, queues)
  are exactly that.
- **A payload is one pointer-width leaf** and a result likewise (`threads.md`; `thread-payloads.md` is the design for more), and
  `parallelism.md` §4 says there is no way to give a second thread its own `Heap`.
- **Waking a thread blocked in the `Poller`** is, in `atomics.md` §6.4, "the broker's real problem": a futex does not wake a
  thread that waits in `poller_wait`; a descriptor the poller also watches (an eventfd or a `Pipe`) would, "not designed, not
  measured".

*What was measured* (`docs/benchmark.md`): one core delivered about 1.2 million QoS 0 messages a second while the broker was 63 to
66% busy, so that figure is the load generator's limit, not the broker's; Mosquitto, the comparison that matters, is also one
thread. The cost of single-threading has not shown up as a limit in any cell run.

**What would make it worth doing.** In order of how soon I expect them to bite:
1. **TLS handshakes.** At 3 to 5 ms of CPU each (above) they, not message fan-out, are what a single thread cannot carry. A
   handshake is a function of one slot: a natural first use of a worker thread is to run it there and hand the slot back. That
   needs the worker to say it is done without blocking the poller (an `Atomic` flag read by the loop, plus a `Pipe` to wake it), so
   it waits for atomics stage A0 and the wake design. It would not touch the tables.
2. **Fan-out beyond one core's limit,** which the benchmark has not found, because the generator ran out first.
3. **Sharding by client** across loops: each shard owns its sessions, and a publish for a subscriber on another shard goes through a
   channel (A3). The subscription trie is then either replicated or queried across shards; a design problem, not an engineering one.

**Without threads.** Several broker processes behind a TCP balancer scale connections but split the subscription space, which is
the clustering problem below; `SO_REUSEPORT` would let processes share a port, **not checked** whether lex-sys exposes it.

**What would have to be true.** Atomics A0 and A1 built, a wake mechanism measured, and a measured workload one core cannot carry.

**Authority.** A new `conc` row (`atomics.md` §5 reuses the existing label; **not checked** whether `spawn` adds anything else to
the report) and a mutant that proves the ceiling refuses a program without it. The ceiling lists no `conc` today.

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

This is my recommendation, and the maintainer's to change. It was revised after QoS 2 was built and after lex-sys's TLS server
design appeared.

1. **`$SYS`** and **MQTT 3.1 (level 3, `MQIsdp`)**: small, no authority change. (**QoS 2: done.**)
2. **Authentication**, designed to take a client-certificate identity as well as a password file, so TLS client certificates can
   follow without a second mechanism. The first feature that needs a file label, so it exercises the "ceiling grows by one exact
   row" path with its mutant on something small.
3. **Persistence, retained messages first** (see that section: it can swap places with 2 if restart loss is the bigger problem).
4. **TLS**, when lex-sys steps 1 to 3 are built and reviewed; a terminating proxy until then. **WebSockets** if there are browser
   clients.
5. **Worker threads for TLS handshakes** after TLS and atomics A0 exist; sharding only on a measured need.
6. **MQTT 5** after the session model has settled. **Clustering** only on a stated need.

## What this document does not settle

Whether QoS 2 and `$SYS` are wanted by anyone, the maintainer's tolerance for an `UNBOUNDED` report, and the loss budget for
persistence are questions for the maintainer; they are not mine to assume. None of the costs here is a measurement of this
broker, and the TLS figures are another program's client side.
